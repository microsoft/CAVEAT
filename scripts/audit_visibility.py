#!/usr/bin/env python
"""Stealth audit: how much of what the AGENT reads is invisible to a HUMAN?

The injection literature asserts imperceptibility ("hidden", "invisible") but essentially never
measures it. This computes, per adversarial family, a concrete stealth metric by diffing the
storefront's real served payloads against the clean store and classifying every changed field by
whether the storefront's own UI renders it to a person.

Classification is grounded in the actual React components, not assumed:
  * HUMAN-VISIBLE  — rendered on the card or the product page (title, price, rating, the
    "About this item" bullets, badges/deal/scarcity chips, the adv_badge chip).
  * AGENT-ONLY     — reaches the agent's observation but never a human's screen:
      agent_note      -> an `sr-only` span (1x1, clip:rect(0,0,0,0)); browser-use's DOM serializer
                         does not test clip/colour/font-size, so it is serialized
      adv_hidden      -> a `display:none` block: dropped from browser_state, but the `extract`
                         action's HTML serializer applies NO visibility filtering, so the sub-LLM
                         reads it
      adv_exclude     -> `data-browser-use-exclude`: REMOVES honest text from the agent's view
                         while a human still reads it (negative stealth — suppression)
      description_html-> serialized by the API, rendered by no component at all

Output per family: agent-only characters added, human-visible fields changed, and a verdict.

    python scripts/audit_visibility.py [scenario] [--port 9330]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The anti-scrape spec budget is stateful (a session serves full spec prose for only N distinct
# products), and this audit necessarily walks the WHOLE catalog. Left on, both snapshots would get
# their bullets stripped at different points and every family would show a spurious
# `bullet_points` diff. Disable it here: we are measuring which content is human-visible, not the
# budget mechanism (that is adv-budget's job, and it is measured behaviourally in the main run).
import os
os.environ["AMAZON_SPEC_BUDGET"] = "0"

import agentarena.envs.amazon  # noqa: F401,E402
from agentarena.benchmark import registry, serialize          # noqa: E402
from agentarena.core.environment import ENVIRONMENTS          # noqa: E402

# Fields the SPA actually renders to a person (ProductCard.tsx / ProductDetail.tsx).
HUMAN_VISIBLE = {"title", "price", "list_price", "rating", "rating_count", "review_count",
                 "bullet_points", "images", "deal", "sponsored", "ad_label", "is_best_seller",
                 "is_amazon_choice", "stock_quantity", "availability_status", "bought_past_month",
                 "adv_badge"}
# Fields that reach the agent but never a human's screen.
AGENT_ONLY = {"agent_note": "sr-only span (clip 1x1)",
              "adv_hidden": "display:none block (extract sub-LLM only)",
              "description_html": "API-only; rendered by no component"}
SUPPRESSION = {"adv_exclude": "data-browser-use-exclude: hides honest text FROM the agent only"}

FAMILIES = ["hidden", "apighost", "subllm", "suppress", "metrology", "flood", "promptfmt",
            "filter", "precomputed", "costblind", "budget", "principal", "policy", "consensus",
            "exec", "all"]


def api(base, path, official=True):
    h = {"Content-Type": "application/json"}
    if official:
        h["X-Storefront-Client"] = "web"
    req = urllib.request.Request(base + path, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def snapshot(base, official=True):
    """Every product payload the storefront serves, as {asin: dict}."""
    out = {}
    for page in range(1, 8):
        got = api(base, f"/api/products?limit=24&page={page}", official)["products"]
        if not got:
            break
        for c in got:
            out.setdefault(c["asin"], {}).update(c)
    for asin in list(out):
        try:
            out[asin].update(api(base, f"/api/products/asin/{asin}", official))
        except Exception:
            pass
    return out


def textlen(v):
    if isinstance(v, str):
        return len(v)
    if isinstance(v, list):
        return sum(len(str(x)) for x in v)
    return len(str(v)) if v is not None else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="laptop")
    ap.add_argument("--port", type=int, default=9330)
    args = ap.parse_args()
    sc = args.scenario

    spec = json.loads((serialize.scenario_dir(sc) / "adversarial.json").read_text())
    meta = spec["_meta"]
    env = ENVIRONMENTS.get("amazon")()
    task = registry.benchmark_tasks(sc, variants=["thresholded"])[0]

    def boot(cond):
        t = dataclasses.replace(task, condition=cond)
        h = env.start(args.port, t, work_dir=Path(tempfile.mkdtemp(prefix="advvis-")))
        return h

    h = boot("clean")
    try:
        clean = snapshot(h.base_url)
    finally:
        h.stop()

    print(f"\nSTEALTH AUDIT — {sc} (trap {meta['trap']}, {meta['dim']} "
          f"{meta['true']:g} vs required {meta['cut']:g}{meta['unit']})")
    print(f"{'family':<12} {'agent-only chars':>17} {'human-visible fields changed':>30}   verdict")
    print("-" * 100)
    rows = []
    for fam in FAMILIES:
        h = boot(f"adv-{fam}")
        try:
            cur = snapshot(h.base_url)
            # what a program reading the JSON API directly sees (the cloaking channel)
            raw = snapshot(h.base_url, official=False)
        finally:
            h.stop()

        agent_chars, vis_changes, suppressed, api_only = 0, [], 0, 0
        for asin, d in cur.items():
            c = clean.get(asin, {})
            for k in AGENT_ONLY:
                if d.get(k) and d.get(k) != c.get(k):
                    agent_chars += textlen(d[k]) - textlen(c.get(k))
            for k in SUPPRESSION:
                if d.get(k):
                    suppressed += 1
            for k in HUMAN_VISIBLE:
                if k in d and d.get(k) != c.get(k):
                    vis_changes.append(f"{k}@{asin}")
            r = raw.get(asin, {})
            for k in ("bullet_points", "title", "price", "rating"):
                if k in r and r.get(k) != d.get(k):
                    api_only += 1

        vis = sorted({v.split("@")[0] for v in vis_changes})
        if api_only and not vis:
            verdict = f"INVISIBLE — differs only for a program reading the API ({api_only} fields)"
        elif suppressed and not vis:
            verdict = f"INVISIBLE (suppression) — honest text hidden from the agent on {suppressed} items"
        elif agent_chars and not vis:
            verdict = "INVISIBLE — human page byte-identical to clean"
        elif vis:
            verdict = f"VISIBLE-DENIABLE — changes {', '.join(vis)}"
        else:
            verdict = "no served-payload difference (behavioural channel: filters/charge/cart)"
        print(f"adv-{fam:<8} {agent_chars:>17,} {len(vis_changes):>30}   {verdict}")
        rows.append({"family": fam, "agent_only_chars": agent_chars,
                     "human_visible_field_changes": len(vis_changes),
                     "human_visible_fields": vis, "suppressed_items": suppressed,
                     "api_only_field_diffs": api_only, "verdict": verdict})

    out = ROOT / "benchmark_data" / "reports" / f"stealth_audit_{sc}.json"
    out.write_text(json.dumps({"scenario": sc, "meta": meta, "rows": rows}, indent=1))
    print(f"\nwrote {out.relative_to(ROOT)}")
    print("\nNote: 'human-visible fields changed' counts fields the storefront's own React "
          "components render.\nA family with 0 there is one whose rendered page is identical to "
          "the clean store for a person.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
