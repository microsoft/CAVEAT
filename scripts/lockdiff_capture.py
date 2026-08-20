#!/usr/bin/env python
"""Lock-proof capture: canonical API snapshot of the EXISTING (original-five) conditions, taken
before and after an engine edit — any difference means a guarded branch leaked into a measured
path and the change must be reverted.

  python scripts/lockdiff_capture.py before   # snapshot on current code
  python scripts/lockdiff_capture.py after    # snapshot + diff vs the 'before' file

Captures, per (scenario, condition): the full paginated SERP (all fields), /api/steering, 6 PDPs
(hero + 2 lures + 3 others), checkout math for the hero and a fee'd lure, and — added for the
hard-mode ("serving object") work — the surfaces that change ONLY if a hard-mode branch leaks:

  * the LEAK endpoints the hard tier clamps: ``/products/{id}/related?limit=1000``,
    ``/products/{id}/similar?limit=1000`` and ``/sellers/1/products?page=1..4&limit=100``.
    Today these are unbounded and unsteered; after the clamp they must still be byte-identical
    for the originals (whose catalog.json has no ``serving`` key).
  * a PAGE-9 request, beyond the legacy 7-page clamp. ``serving.pages`` rewrites exactly that
    arithmetic, so page 9 is where a mis-gated pagination change shows up first.

Together these turn "the new branch is dead for the originals" from an assertion into a
MEASUREMENT. Volatile keys (timestamps, order numbers, row ids) are stripped recursively.

The capture covers clean, all eight measured primitive steering conditions, and combined.
It also hashes every committed artifact for the five original scenarios plus the regenerated
server catalog/steering sidecars.  A green API diff with changed catalog bytes is not a lock.

EVIDENCE CHAIN (added 2026-07-26). A lockdiff PASS only means something if the ``before``
snapshot was taken BEFORE the engine edit; the first hard-mode capture was taken three minutes
AFTER it, so it proved "stable since 10:43", not "identical to pre-edit". Both snapshots now
carry a ``__meta__`` block with the sha256 + mtime of every engine file, and ``after`` reports:

  * which engine files actually changed between the two captures — if NONE did, the comparison
    is vacuous and the run exits ``2`` (LOCKDIFF INCONCLUSIVE) instead of a green PASS;
  * whether the ``before`` snapshot itself post-dates a recent edit to one of those files, in
    which case it says so explicitly rather than letting a PASS imply more than it proves.

The standing replacement for the arithmetic this script used to be the only witness of is
the legacy serving-contract check, which re-derives the serving
window (limit/skip clamps, the page-9 repeat, the block insert and its tail collapse, the
unbounded rails, ``max_pages() is None``) on every pytest run.

Env: ``LOCKDIFF_OUT`` (default ``results/lockdiff``), ``LOCKDIFF_PORT`` (default 10410 — a live
campaign owns everything below 10400), ``LOCKDIFF_STALE_MIN`` (default 60 — how recent an
engine edit has to be, relative to the ``before`` capture, to call the baseline into question).
"""
import dataclasses
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import caveat.envs.caveat_shop  # noqa: F401,E402
from caveat.core.environment import ENVIRONMENTS  # noqa: E402
from caveat.benchmark import registry, serialize  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("LOCKDIFF_PORT") or 10410)
OUT = Path(os.environ.get("LOCKDIFF_OUT") or (REPO / "results" / "lockdiff"))
ORIGINAL_SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
ORIGINAL_CONDITIONS = (
    "clean",
    "sponsored", "ranking", "drip", "promo", "addon", "scarcity", "trust", "friction",
    "combined",
)
# The NON-NEGOTIABLE invariant: all five originals x clean + eight measured arms + combined
# must be byte-identical.
MATRIX = [(s, ORIGINAL_CONDITIONS) for s in ORIGINAL_SCENARIOS]
VOLATILE = {"created_at", "updated_at", "order_number", "order_date", "estimated_delivery",
            "estimated_delivery_min", "estimated_delivery_max", "session_token", "id",
            "cart_id", "order_id", "date"}


# Every file that can change what an ORIGINAL scenario serves, plus the
# harness-only integration surfaces whose isolation this lockdiff is proving.
# A comparison is informative only when at least one listed file changed.
ENGINE_FILES = (
    "caveat/scaffolds/browseruse.py",
    "caveat/scaffolds/caveat_harness.py",
    "caveat/scaffolds/_caveat_harness_core.py",
    "caveat/core/experiment.py",
    "caveat/core/environment.py",
    "caveat/benchmark/registry.py",
    "caveat/benchmark/schema.py",
    "caveat/benchmark/serialize.py",
    "caveat/benchmark/validate.py",
    "caveat/scoring/optimal_selection.py",
    "caveat/envs/_storefront/gate.py",
    "caveat/envs/_storefront/placement.py",
    "caveat/envs/caveat_shop/__init__.py",
    "caveat/envs/caveat_shop/catalog.py",
    "caveat/envs/caveat_shop/server/backend/app.py",
    "caveat/envs/caveat_shop/server/backend/counting.py",
    "caveat/envs/caveat_shop/server/backend/routes.py",
    "caveat/envs/caveat_shop/server/backend/experiment_laptops.py",
    "caveat/envs/caveat_shop/server/backend/seed.py",
    "caveat/envs/caveat_shop/server/backend/ssr.py",
    "caveat/envs/caveat_shop/server/backend/adversarial.py",
    # Intentionally absent in the pre-successor baseline. None -> a real hash after the edit
    # makes the provenance comparison meaningful even if the integration points stay tiny.
    "caveat/envs/caveat_shop/server/backend/truthful.py",
)
STALE_MIN = float(os.environ.get("LOCKDIFF_STALE_MIN") or 60)

# The hard tier is backend-only and selects the existing classic SSR transport.
# Rebuilding the one shared frontend would change every original condition even if the API
# lock stayed green, so pin the exact pre-edit dist bytes.
FRONTEND_DIST = REPO / "caveat" / "envs" / "caveat_shop" / "server" / "frontend" / "dist"
FRONTEND_SHA256 = {
    "assets/index-IwcoZ3da.css":
        "165bdb68f335fa807951d57028a08945b478c988479df1890489a6048f3f1c21",
    "assets/index-JAav4Fab.js":
        "c62007f67c38fda8986b99cefe7e95e65cb38654378f2e482084346bf1344498",
    "index.html":
        "1339458bfa7517c8e85b315194ccafede7805621b277f324fff7fae7b54efd7d",
}


def _report_frontend() -> list[str]:
    actual = {}
    if FRONTEND_DIST.exists():
        for p in sorted(FRONTEND_DIST.rglob("*")):
            if p.is_file():
                actual[str(p.relative_to(FRONTEND_DIST))] = hashlib.sha256(
                    p.read_bytes()).hexdigest()
    changed = sorted(k for k in set(actual) | set(FRONTEND_SHA256)
                     if actual.get(k) != FRONTEND_SHA256.get(k))
    if changed:
        print(f"  frontend bytes: FAIL — shared prebuilt bundle drifted: {changed}")
    else:
        print(f"  frontend bytes: PASS — {len(actual)} pre-edit dist files byte-identical")
    return changed


def provenance() -> dict:
    """sha256 + mtime of every engine file, plus the capture time.

    This is the evidence chain: without it a ``before`` snapshot taken after the edit is
    indistinguishable from one taken before it, and the resulting PASS says nothing.
    """
    files = {}
    for rel in ENGINE_FILES:
        p = REPO / rel
        if not p.exists():
            files[rel] = {"sha256": None, "mtime": None}
            continue
        files[rel] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
                      "mtime": round(p.stat().st_mtime, 3)}
    return {"captured_at": round(time.time(), 3), "files": files}


def original_artifact_manifest() -> dict:
    """Full-byte manifest for every original task artifact and regenerated server sidecar."""
    paths = []
    for sid in ORIGINAL_SCENARIOS:
        d = serialize.DATA_ROOT / sid
        if d.exists():
            paths.extend(p for p in d.rglob("*") if p.is_file())
        cat = REPO / "caveat" / "envs" / "caveat_shop" / "server" / "_catalogs"
        paths.append(cat / f"{sid}.json")
        paths.extend(cat / f"{sid}.{cond}.steering.json" for cond in ORIGINAL_CONDITIONS)
    out = {}
    for p in sorted(set(paths)):
        rel = str(p.relative_to(REPO))
        if not p.exists():
            out[rel] = {"sha256": None, "size": None}
            continue
        raw = p.read_bytes()
        out[rel] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    return out


def _report_provenance(before: dict, after: dict) -> bool:
    """Print what moved between the captures. Returns True when the comparison is MEANINGFUL
    (at least one engine file changed), False when it is vacuous."""
    bm, am = (before or {}).get("__meta__") or {}, (after or {}).get("__meta__") or {}
    bf, af = bm.get("files") or {}, am.get("files") or {}
    if not bf:
        print("  provenance: the 'before' snapshot predates evidence-chain capture — its "
              "engine state is UNKNOWN; re-take it to get a checkable baseline")
        return True
    changed = sorted(f for f in ENGINE_FILES
                     if (bf.get(f) or {}).get("sha256") != (af.get(f) or {}).get("sha256"))
    stale = sorted(f for f in ENGINE_FILES
                   if (bf.get(f) or {}).get("mtime")
                   and 0 <= bm.get("captured_at", 0) - bf[f]["mtime"] <= STALE_MIN * 60)
    print(f"  provenance: {len(changed)} engine file(s) changed between the captures"
          + (f": {', '.join(changed)}" if changed else ""))
    if stale:
        mins = {f: round((bm["captured_at"] - bf[f]["mtime"]) / 60, 1) for f in stale}
        print(f"  provenance: the 'before' snapshot was taken within {STALE_MIN:.0f} min of an "
              f"edit to {mins} — it proves stability SINCE then, not pre-edit identity")
    return bool(changed)


def _report_original_artifacts(before: dict, after: dict) -> list[str]:
    bm = ((before or {}).get("__meta__") or {}).get("original_artifacts") or {}
    am = ((after or {}).get("__meta__") or {}).get("original_artifacts") or {}
    changed = sorted(k for k in set(bm) | set(am) if bm.get(k) != am.get(k))
    if changed:
        print(f"  original bytes: FAIL — {len(changed)} artifact(s) changed")
        for rel in changed:
            print(f"    {rel}: {bm.get(rel)} -> {am.get(rel)}")
    else:
        print(f"  original bytes: PASS — {len(am)} artifact(s) byte-identical")
    return changed


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in sorted(x.items()) if k not in VOLATILE}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    return x


_OPS = {"token": ""}     # set per cell from handle.env — see capture()


def api(base, path, method="GET", body=None):
    """One evaluator-side read.

    Presents the OPS token (``X-Storefront-Ops``): a lock snapshot is an EVALUATOR read, not an
    agent read — the ops path bypasses the storefront gate and is never rate-counted, so captures
    are deterministic and can never be throttled into a spurious MISMATCH. (Before the 2026-07 API
    lockdown there was no gate and this script sent no credential at all; without the token every
    request now 403s.)

    NEVER lets an HTTPError escape with a live response object: the failing socket stays connected
    to 127.0.0.1:<port>, and ``handle.stop()``'s ``fuser -k <port>/tcp`` matches REMOTE ports too —
    so an in-flight exception through the ``finally`` gets THIS process SIGKILLed. Drain, close,
    and return the status as data.
    """
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          "X-Storefront-Ops": _OPS["token"]})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode("utf-8", "replace")[:300]
        finally:
            e.close()
        return {"__status__": e.code, "__body__": body_txt}
    except Exception as e:  # noqa: BLE001
        return {"__error__": f"{type(e).__name__}: {e}"}


def _try(base, path):
    """Capture an endpoint that may legitimately 404/500 — the STATUS is part of the snapshot."""
    return api(base, path)


def capture(scen, cond):
    env = ENVIRONMENTS.get("caveat_shop")()
    task = dataclasses.replace(registry.benchmark_tasks(scen, variants=["graded4"])[0],
                               condition=cond)
    h = env.start(PORT, task, work_dir=Path(tempfile.mkdtemp(prefix="lockdiff-")))
    _OPS["token"] = h.env.get("STOREFRONT_OPS_TOKEN") or ""
    try:
        base = h.base_url
        cap = {}
        serp = []
        for page in range(1, 8):
            ps = api(base, f"/api/products?limit=24&page={page}").get("products") or []
            if not ps:
                break
            serp.extend(ps)
        cap["serp"] = serp
        cap["steering"] = api(base, "/api/steering")
        spec = json.loads((serialize.scenario_dir(scen) / "steering.json").read_text())
        lures = (spec.get("combined") or {}).get("decoy_skus", [])[:2]
        cat = env.catalog(scen)
        hero = next((p.asin for p in cat.products if p.role == "compliant"
                     and getattr(p, "decoy_kind", None) == "hero"),
                    next(p.asin for p in cat.products if p.role == "compliant"))
        pdp_asins = [hero] + lures + [c["asin"] for c in serp[5:8]]
        cap["pdp"] = {a: api(base, f"/api/products/asin/{a}") for a in pdp_asins}

        # ---- pagination beyond the legacy 7-page clamp ------------------------------- #
        # `serving.pages` rewrites exactly this arithmetic (_serving_window), so a mis-gated
        # pagination change surfaces here first. Empty for the originals — and must STAY empty.
        cap["page9"] = api(base, "/api/products?limit=24&page=9&offset=192")
        cap["page8"] = api(base, "/api/products?limit=24&page=8&offset=168")

        # ---- LEAK endpoints the hard tier clamps ------------------------------------- #
        # /related + /similar take an unbounded `limit` and are UNSTEERED (ORDER BY rating DESC,
        # i.e. the hero near the top); /sellers/{id}/products takes an unbounded `page` and
        # bypasses the page cap entirely. All three are clamped + steered under `serving.rails`;
        # for the originals every byte below must be unchanged.
        by_asin = {c["asin"]: c["id"] for c in serp}
        hero_id = by_asin.get(hero)
        rails = {}
        for label, pid in (("hero", hero_id), ("first", serp[0]["id"] if serp else None)):
            if pid is None:
                continue
            rails[f"related/{label}"] = _try(base, f"/api/products/{pid}/related?limit=1000")
            rails[f"similar/{label}"] = _try(base, f"/api/products/{pid}/similar?limit=1000")
        for page in range(1, 5):
            rails[f"seller1/page{page}"] = _try(
                base, f"/api/sellers/1/products?page={page}&limit=100")
        cap["rails"] = rails

        flows = {}
        for label, asin in (("hero", hero), ("lure", lures[0] if lures else hero)):
            pid = next(c["id"] for c in serp if c["asin"] == asin)
            api(base, "/api/cart/items", "POST", {"product_id": pid, "quantity": 1})
            api(base, "/api/checkout/start", "POST", {})
            flows[label] = {"cart": api(base, "/api/cart"),
                            "summary": api(base, "/api/checkout/summary")}
            api(base, "/api/cart", "DELETE")
        cap["flows"] = flows
        return scrub(cap)
    finally:
        h.stop()


def main(phase):
    snap = {"__meta__": provenance()}
    for scen, conds in MATRIX:
        for cond in conds:
            print(f"capturing {scen}/{cond} ...", flush=True)
            snap[f"{scen}/{cond}"] = capture(scen, cond)
    # Capture this AFTER the matrix: each run atomically regenerates the catalog and condition
    # sidecar, so the manifest proves the bytes emitted by the code under test, not stale files.
    snap["__meta__"]["original_artifacts"] = original_artifact_manifest()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"lockdiff_{phase}.json"
    path.write_text(json.dumps(snap, sort_keys=True))
    print(f"wrote {path}")
    frontend_bad = _report_frontend()
    if phase == "after":
        before = json.loads((OUT / "lockdiff_before.json").read_text())
        bad = 0
        # `__meta__` is the evidence chain, not a measured surface: it is SUPPOSED to differ.
        for k in sorted((set(before) | set(snap)) - {"__meta__"}):
            b, a = before.get(k), snap.get(k)
            if json.dumps(b, sort_keys=True) == json.dumps(a, sort_keys=True):
                continue
            bad += 1
            # name the surface that moved (serp / pdp / rails / page8 / page9 / flows / steering)
            sub = sorted({s for s in set(list(b or {}) + list(a or {}))
                          if json.dumps((b or {}).get(s), sort_keys=True)
                          != json.dumps((a or {}).get(s), sort_keys=True)}) \
                if isinstance(b, dict) and isinstance(a, dict) else ["<whole capture>"]
            print(f"MISMATCH: {k}  ->  {', '.join(sub)}")
        n = len(snap) - 1
        meaningful = _report_provenance(before, snap)
        artifact_bad = _report_original_artifacts(before, snap)
        if bad or artifact_bad or frontend_bad:
            print(f"LOCKDIFF FAIL — {bad} condition(s) and {len(artifact_bad)} original "
                  f"artifact(s), {len(frontend_bad)} frontend file(s) changed; "
                  f"REVERT the engine edit")
            return 1
        if not meaningful:
            print(f"LOCKDIFF INCONCLUSIVE — all {n} conditions are identical, but NO engine "
                  f"file changed between the two captures, so this run proves nothing about "
                  f"the edit. Re-take 'before' on the pre-edit tree.")
            return 2
        print(f"LOCKDIFF PASS — all {n} existing conditions byte-identical across an engine "
              f"change")
        return 0
    return 1 if frontend_bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "before"))
