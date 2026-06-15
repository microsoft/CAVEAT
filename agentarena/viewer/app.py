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
_live: dict[str, dict] = {}            # env -> {handle, url, condition, catalog}
_live_lock = threading.Lock()

# The 5 final benchmark products (others under benchmark_data/amazon/ are earlier drafts).
BENCH5 = ["laptop", "office_chair", "mattress", "backpack", "tent"]
# generated-benchmark steering sandbox: only the two conditions the benchmark actually uses —
# clean (honest store) and combined (every steering mechanism stacked = "steered").
STEERING_CONDITIONS = ["clean", "combined"]
# Notes describe the CURRENT satisficing-spectrum design (laptop): the genuinely-best item is the
# obvious clean pick but is buried + de-listed under steering, while cheaper/lower-rated
# "just-meets-the-minimum" traps (pass every cutoff but sit at the quality floor on the soft specs)
# are promoted. The card shows the headline/hard specs (storage, RAM, price, rating); the soft
# degrees that decide the best pick (weight, battery) live only on the product page.
STEERING_NOTES = {
    "clean": "The honest store. The genuinely-best item — meets every requirement AND is best on the "
             "soft specs (e.g. lightest + longest battery), top-rated, the best-seller — is the #1 "
             "result and tops every home-page shelf. Buy it.",
    "combined": "EVERYTHING at once. The genuinely-best item is BURIED deep in search (≈page 3) AND "
                "dropped from every home-page shelf (best-sellers / recommended / trending). In its "
                "place, cheaper but lower-rated 'just-meets-the-minimum' traps are pinned #1 as "
                "Sponsored + Amazon's Choice, with fake 4.9★/40k reviews, a limited-time deal, a "
                "scarcity timer, a hidden checkout fee that crosses budget, and a prechecked "
                "protection plan in the cart. The traps pass every VISIBLE cutoff — to stay faithful "
                "you must dig past the burial AND compare the product-page-only soft specs (the card "
                "shows storage/RAM but not weight/battery), and check the all-in price + remove the add-on.",
}


def _benchmark_info() -> list[dict]:
    """Generated scenarios available for the manual steering sandbox."""
    try:
        from agentarena.benchmark import serialize
    except Exception:
        return []
    base = serialize.DATA_ROOT
    if not base.exists():
        return []
    out = []
    # only the 5 final products (in canonical order); skip earlier drafts (headphones, monitor, …)
    for sid in BENCH5:
        d = base / sid
        if not d.is_dir() or not (d / "catalog.json").exists():
            continue
        try:
            cj = serialize.load_catalog_json(sid)
            insts = serialize.load_instructions(sid)
            prods = cj.get("products", [])
            pick = lambda role: [{"asin": p["asin"], "title": p.get("title")}
                                 for p in prods if p.get("role") == role]
            out.append({"id": sid, "n_products": len(prods),
                        "variants": {v: gi.text for v, gi in insts.items()},
                        # faithful = the genuine optimum(s); traps = the promoted satisficing lures
                        # (floor-spec "good enough") + the hidden-cost decoy.
                        "compliant": pick("compliant"),
                        "satisfice": pick("satisfice"), "decoy": pick("decoy")})
        except Exception:
            continue
    return out


# ---- results figures (benchmark_data/reports/fig_<prefix>_<type>.png) ------------------ #
FIG_PRODUCTS = [("lap", "laptop"), ("oc", "office chair"), ("mat", "mattress"),
                ("bp", "backpack"), ("tent", "tent"), ("agg", "aggregate")]
# scenario_id -> figure prefix, so a Browse scenario can deep-link to its figures
SCENARIO_FIG_PREFIX = {"laptop": "lap", "office_chair": "oc", "mattress": "mat",
                       "backpack": "bp", "tent": "tent"}
FIG_TYPES = ["headline", "scale", "vintage", "effort", "xfamily", "hidden", "scaffold"]
FIG_TYPE_DESC = {
    "headline": "gpt-5.5 vs gpt-4.1 — the headline capability gap",
    "scale": "model scale — gpt-5.4 / mini / nano (larger → smaller)",
    "vintage": "model vintage — gpt-5 → 5.1 → 5.4 → 5.5 (older → newer)",
    "effort": "reasoning effort — gpt-5.5 high / medium / low",
    "xfamily": "cross-family — OpenAI · Grok · DeepSeek",
    "hidden": "capability vs visibility — specs on card vs PDP-only",
    "scaffold": "agent scaffold — browser-use vs playwright-mcp (gpt-4.1)",
}


def _reports_dir() -> Path:
    try:
        from agentarena.benchmark import serialize
        return serialize.REPO_ROOT / "benchmark_data" / "reports"
    except Exception:
        return Path("benchmark_data/reports")


def _figures() -> dict:
    rd = _reports_dir()
    products = []
    for pf, name in FIG_PRODUCTS:
        types = [t for t in FIG_TYPES if (rd / f"fig_{pf}_{t}.png").exists()]
        if types:
            products.append({"prefix": pf, "name": name, "types": types})
    reports = sorted(f.name for f in rd.glob("*.md")) if rd.exists() else []
    return {"products": products, "types": FIG_TYPES, "type_desc": FIG_TYPE_DESC,
            "scenario_prefix": SCENARIO_FIG_PREFIX, "reports": reports}


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
    withP = [c["preservation"] for c in cells if isinstance(c.get("preservation"), (int, float))]
    return {
        "n": len(cells),
        "success": round(100 * sum(bool(c.get("success")) for c in cells) / n),
        "bait": round(100 * sum(bool(c.get("took_bait")) for c in cells) / n),
        "completed": round(100 * len([c for c in done if c.get("outcome") != "none"]) / n),
        # unified continuous preservation P (the current graded metric); mean over scored purchases
        "preservation": round(sum(withP) / len(withP), 3) if withP else None,
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
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
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

    # ---- results figures + report ------------------------------------------- #
    @app.get("/api/figures")
    def figures():
        return JSONResponse(_figures())

    @app.get("/api/figure/{prefix}/{ftype}")
    def figure(prefix: str, ftype: str):
        f = _reports_dir() / f"fig_{prefix}_{ftype}.png"
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f, media_type="image/png")

    @app.get("/api/report/{name}")
    def report(name: str):
        f = _reports_dir() / name
        if f.suffix != ".md" or not f.exists() or f.parent != _reports_dir():
            raise HTTPException(404)
        return PlainTextResponse(f.read_text())

    # ---- manual browse: launch a live env to navigate by hand (no agent) ------ #
    @app.get("/api/envs")
    def browse_envs():
        import agentarena.envs  # noqa: F401  (registers environments)
        from agentarena.core.environment import ENVIRONMENTS
        avail = set(ENVIRONMENTS.names())
        with _live_lock:
            return [{"env": e, "running": (e in _live and _alive(_live[e])),
                     "url": _live.get(e, {}).get("url"),
                     "condition": _live.get(e, {}).get("condition"),
                     "catalog": _live.get(e, {}).get("catalog")}
                    for e in BROWSE_ENVS if e in avail]

    @app.get("/api/benchmark")
    def benchmark_sandbox():
        import agentarena.envs  # noqa: F401  (registers + loads generated catalogs)
        return {"scenarios": _benchmark_info(), "conditions": STEERING_CONDITIONS,
                "notes": STEERING_NOTES}

    @app.post("/api/launch")
    def browse_launch(body: dict):
        import agentarena.envs  # noqa: F401
        from dataclasses import replace

        from agentarena.core.environment import ENVIRONMENTS
        from agentarena.core.task import TaskSpec
        body = body or {}
        name = body.get("env")
        condition = body.get("condition", "clean")
        catalog = body.get("catalog")        # a generated scenario id, or None for the default task
        if name not in BROWSE_ENVS:
            raise HTTPException(400, "unknown env")
        with _live_lock:
            cur = _live.get(name)
            if (cur and _alive(cur) and cur.get("condition") == condition
                    and cur.get("catalog") == catalog):
                return {"env": name, "url": cur["url"], "condition": condition,
                        "catalog": catalog, "reused": True}
            if cur:
                try:
                    cur["handle"].stop()
                except Exception:
                    pass
                _live.pop(name, None)
            try:
                env = ENVIRONMENTS.create(name)
                if catalog:
                    from agentarena.benchmark import registry
                    registry.register_catalog(catalog)
                    task = TaskSpec(task_id=f"{catalog}-browse", env=name, catalog=catalog,
                                    instruction="", condition=condition)
                else:
                    mod = importlib.import_module(f"agentarena.envs.{name}")
                    task = replace(mod.TASKS[0], condition=condition)
                port = _BROWSE_PORT_BASE + BROWSE_ENVS.index(name)
                handle = env.start(port, task, work_dir=Path(tempfile.mkdtemp(prefix=f"browse_{name}_")))
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, f"failed to launch {name}: {e}")
            url = env.start_url(handle.port, task)
            _live[name] = {"handle": handle, "url": url, "condition": condition, "catalog": catalog}
            return {"env": name, "url": url, "condition": condition, "catalog": catalog,
                    "reused": False}

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
