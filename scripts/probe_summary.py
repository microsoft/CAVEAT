"""Summarize a steering probe: per (model, variant) clean vs combined preservation, the gap, and
completion rate. Reads summary.json under one or more result globs.

Usage: python scripts/probe_summary.py results/oc_g41_r* results/oc_g55_r*
"""
import glob
import json
import sys
from collections import defaultdict

VARS = ["thresholded", "mixed", "graded", "graded3", "graded4"]


def collect(globs):
    # data[(model, variant, condition)] = ([P...], n_total, n_complete)
    P = defaultdict(list)
    tot = defaultdict(int)
    comp = defaultdict(int)
    seen = set()
    for g in globs:
        for sf in glob.glob(g + "/*/summary.json"):
            try:
                d = json.load(open(sf))
            except Exception:
                continue
            m = d.get("model")
            tid = d.get("task_id", "")
            v = tid.rsplit("-", 1)[-1]
            c = d.get("condition")
            key = (d.get("seconds"), d.get("preservation"), m, v, c, sf.split("/")[-2])
            if key in seen:
                continue
            seen.add(key)
            p = d.get("preservation")
            tot[(m, v, c)] += 1
            if isinstance(p, (int, float)):
                P[(m, v, c)].append(p)
                comp[(m, v, c)] += 1
    return P, tot, comp


def main():
    globs = sys.argv[1:] or ["results/oc_g41_r*", "results/oc_g55_r*"]
    P, tot, comp = collect(globs)
    models = sorted({k[0] for k in tot})
    mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
    for m in models:
        print(f"\n=== {m} ===")
        print(f"  {'variant':12s} {'clean':>14s} {'combined':>14s} {'gap':>7s}   completion(clean/comb)")
        for v in VARS:
            cl = P.get((m, v, "clean"), [])
            cb = P.get((m, v, "combined"), [])
            mc, mb = mean(cl), mean(cb)
            gap = (mc - mb) if (cl and cb) else float("nan")
            cct = f"{comp.get((m,v,'clean'),0)}/{tot.get((m,v,'clean'),0)}"
            cbt = f"{comp.get((m,v,'combined'),0)}/{tot.get((m,v,'combined'),0)}"
            print(f"  {v:12s} {mc:8.2f}(n={len(cl):2d}) {mb:8.2f}(n={len(cb):2d}) {gap:7.2f}   {cct} / {cbt}")


if __name__ == "__main__":
    main()
