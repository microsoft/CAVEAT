#!/usr/bin/env python
"""Compute the candidate strict-metric family for the harvested-clone (storefront) envs
over results/byenv_v2 -> benchmark_data/reports/scoring_variants_8env.json.

Mirrors the storefront eval path EXACTLY (caveat/envs/_storefront/adapter.py):
chosen attrs = catalog item attrs with price overridden to the recorded all-in
(paid + add-ons); gate = ALL hard preference fields of the task (storefront
must_have_fields convention — note this differs from caveat_shop's across-variant
intersection); O terms = the task's graded dims. READ-ONLY over results/.

Hard gate: recomputed v0 must reproduce the stored details.preservation_strict on
every scored cell (|d| <= 5e-4) — this doubles as the stale-catalog guard, since the
envs were locked after measurement.

  .venv/bin/python scripts/score_variants_8env.py
"""
import glob
import importlib
import json
import math
import os
import sys
from collections import Counter

sys.path.insert(0, __file__.rsplit("scripts/", 1)[0] or ".")  # repo root (portable: works from any checkout)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # scripts/ (shared rule)
from caveat.core.environment import ENVIRONMENTS  # noqa: E402
from caveat.core.task import check_constraints  # noqa: E402
from caveat.envs._storefront.scoring import must_have_fields  # noqa: E402
from caveat.scoring.continuous import (STRICT_GAMMA, score_criteria,  # noqa: E402
                                           strict_preservation)
# Infra-vs-capability is decided by the ONE shared implementation in scripts/_infra_classify.py
# (build_figure_data.py imports the same module). Here it only TAGS each
# record (rec["infra"]); nothing is dropped from the emitted dataset.
from _infra_classify import is_infra_fail  # noqa: E402

RESULTS = os.environ.get("PILOT_RESULTS", "results/byenv_v2")
OUT = "benchmark_data/reports/scoring_variants_8env.json"
_EPS = 1e-6


_env_cache: dict = {}


def env_ctx(env_name: str):
    """(tasks_by_id, env_instance) with the env module imported for registration."""
    if env_name not in _env_cache:
        mod = importlib.import_module(f"caveat.envs.{env_name}")
        env = ENVIRONMENTS.create(env_name)
        _env_cache[env_name] = ({t.task_id: t for t in mod.TASKS}, env)
    return _env_cache[env_name]


_task_cache: dict = {}


def task_ctx(env_name: str, task_id: str):
    """(task, items, cands, mh, graded, cand_table, o_best) for one (env, task).
    caveat_stay is the one non-storefront shape: Catalog.listings keyed by TITLE (the adapter
    books by listing id and records chosen_label=title), no addon roles."""
    key = (env_name, task_id)
    if key not in _task_cache:
        tasks, env = env_ctx(env_name)
        t = tasks[task_id]
        cat = env.catalog(t.catalog) or next(iter(env.catalogs.values()))
        if hasattr(cat, "items"):
            items = {it.sku: it for it in cat.items if getattr(it, "role", "") != "addon"}
        else:
            items = {l.title: l for l in cat.listings}
        cands = [it.attrs() for it in items.values()]
        mh = must_have_fields(t.preferences)
        graded = (getattr(t, "metadata", None) or {}).get("graded", {}) or {}
        tab = {}
        for sku, it in items.items():
            a = it.attrs()
            cs = score_criteria(a, t.preferences, graded, cands)
            terms = [c["s"] for k, c in cs.per_criterion.items() if c.get("class") == "grd"]
            o = (sum(s ** STRICT_GAMMA for s in terms) / len(terms)) if terms else 1.0
            tab[sku] = (not check_constraints(a, t.preferences), o)
        o_best = max((o for ok, o in tab.values() if ok), default=1.0)
        _task_cache[key] = (t, items, cands, mh, graded, tab, o_best)
    return _task_cache[key]


def cell_record(tj: str):
    d = json.load(open(tj))
    env_name, tid = d.get("env"), d.get("task_id", "")
    ev = d.get("evaluation") or {}
    det = ev.get("details") or {}
    rec = {"env": env_name, "model": d.get("model"), "scaffold": d.get("scaffold"),
           "condition": d.get("condition"), "task_id": tid,
           "variant": tid.rsplit("-", 1)[-1], "rep": os.path.basename(os.path.dirname(os.path.dirname(tj))),
           "outcome": ev.get("outcome"), "infra": is_infra_fail(os.path.dirname(tj)),
           "stored_strict": det.get("preservation_strict"),
           "components": None, "metrics": None}
    chosen = ev.get("chosen")
    if rec["outcome"] in ("error", "skipped", "none", None) or chosen is None:
        return rec, "no_purchase"
    t, items, cands, mh, graded, tab, o_best = task_ctx(env_name, tid)
    item = items.get(chosen if env_name != "caveat_stay" else (ev.get("chosen_label") or ""))
    if item is None or det.get("off_catalog"):
        rec["components"], rec["metrics"] = {"off": True}, {k: 0.0 for k in METRICS}
        return rec, "off_catalog"
    # exact adapter reconstruction. Storefront: catalog attrs, price := recorded all-in
    # (paid unit + add-ons) — fee factor = sticker/all-in. CAVEAT-Stay: attrs + total_price;
    # its service/cleaning fees are UNIFORM across clean and steered (backend: flat 14%
    # service fee for every listing), i.e. not a steering lever -> fee factor 1.
    attrs = item.attrs()
    if env_name == "caveat_stay":
        attrs["total_price"] = det.get("total_price")
        sticker = all_in = None
    else:
        sticker = attrs.get("price")
        all_in = det.get("all_in", det.get("price_paid"))
        if all_in is not None:
            attrs["price"] = float(all_in)
    cs = score_criteria(attrs, t.preferences, graded, cands)

    gate = 1.0
    for k, c in cs.per_criterion.items():
        if c.get("class") == "thr" and c["s"] < 1.0:
            gate = 0.0
    terms = [c["s"] for c in cs.per_criterion.values() if c.get("class") == "grd"]
    m = len(terms)
    prod = math.prod(terms) if terms else 1.0
    o_geo = prod ** (STRICT_GAMMA / m) if (terms and prod > 0) else (0.0 if terms else 1.0)

    comp = {"m": m, "gate": gate, "o_geo": round(o_geo, 6),
            # cross-check only: the recomputed production strict metric must reproduce the value
            # stored in the summary (proves the reconstruction is faithful); not a reported metric.
            "strict_check": round(strict_preservation(cs, mh, soft=False), 4)}
    rec["components"] = comp
    rec["metrics"] = {k: round(f(comp), 4) for k, f in METRICS.items()}
    return rec, "scored"


# vgeo is the sole reported fidelity metric (see caveat/scoring/strict_variants).
METRICS = {
    "vgeo": lambda c: c["gate"] * c["o_geo"],
}


def main():
    recs, cov, bad = [], Counter(), []
    for tj in sorted(glob.glob(f"{RESULTS}/*/*/trajectory.json")):
        try:
            rec, kind = cell_record(tj)
        except Exception as e:  # noqa: BLE001
            cov["failed"] += 1
            bad.append((tj, f"EXC {type(e).__name__}: {e}", None))
            continue
        cov[kind] += 1
        if kind == "scored":
            st = rec["stored_strict"]
            chk = rec["components"]["strict_check"]
            if st is None or abs(chk - float(st)) > 5e-4:
                bad.append((tj, st, chk))
        recs.append(rec)
    for env in sorted({r["env"] for r in recs}):
        n = sum(1 for r in recs if r["env"] == env)
        s = sum(1 for r in recs if r["env"] == env and r["components"] and not r["components"].get("off"))
        print(f"{env:10s} cells={n:4d} scored={s:4d}")
    print(f"coverage: {dict(cov)}")
    if bad:
        print(f"\nFATAL: {len(bad)} cells fail strict reproduction / reconstruction:")
        for tj, st, v0 in bad[:10]:
            print(f"   {tj}: stored={st} recomputed={v0}")
        sys.exit(1)
    json.dump({"metrics": list(METRICS), "cells": recs}, open(OUT, "w"))
    print(f"\nOK: recomputed strict metric reproduces stored preservation_strict on every scored cell.")
    print(f"{OUT}: {len(recs)} records")


if __name__ == "__main__":
    main()
