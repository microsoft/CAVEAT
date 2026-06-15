"""Emit markdown results tables for the 5-product steering benchmark, read from results/.

Per (figure-group, product) it prints a clean/combined/gap table per variant per model, plus a
cross-product AGGREGATE. Used to populate the findings report at the end.

Usage: python scripts/report_tables.py [headline|scale|vintage|effort|xfamily|hidden|all]
"""
import glob
import json
import sys
from collections import defaultdict

VARS = ["thresholded", "mixed", "graded", "graded3", "graded4"]
PREFIX = {"laptop": "lap", "office_chair": "oc", "mattress": "mat", "backpack": "bp", "tent": "tent"}

# (model-label, model-name, glob-template keyed by product prefix). {P} filled per product.
# laptop uses its legacy run names.
def globs(P, kind):
    if P == "lap":
        m = {"g55": ["lap_gN_g55_r*", "lap_gN_g55b_r*"], "g41": ["lap_gN3_g41_r*"],
             "exp": ["lap_vintage5_r*", "lap_vintage5b_r*", "lap_scale54_r*",
                     "lap_effort55_r*", "lap_effort55b_r*", "lap_xfam_r*", "lap_xfam2_r*",
                     "lap_xfam_ds_r*", "lap_xfam_ds2_r*"],
             "hid": ["lap_hidden_r*"]}
    else:
        m = {"g55": [f"{P}_g55_r*"], "g41": [f"{P}_g41_r*"], "exp": [f"{P}_exp_r*"],
             "hid": [f"{P}_hid_r*"]}
    return [f"results/{g}" for g in m[kind]]


GROUPS = {
    "headline": [("gpt-5.5", "gpt-5.5", "g55"), ("gpt-4.1", "gpt-4.1", "g41")],
    "scale": [("gpt-5.4", "gpt-5.4", "exp"), ("5.4-mini", "gpt-5.4-mini", "exp"),
              ("5.4-nano", "gpt-5.4-nano", "exp")],
    # gpt-5.2 dropped (capability limit: fails combined even at low concurrency -> no steered data)
    "vintage": [("gpt-5.5", "gpt-5.5", "g55"), ("gpt-5.4", "gpt-5.4", "exp"),
                ("gpt-5.1", "gpt-5.1", "exp"), ("gpt-5", "gpt-5", "exp")],
    "effort": [("high", "gpt-5.5-high", "exp"), ("medium", "gpt-5.5-medium", "exp"),
               ("low", "gpt-5.5-low", "exp")],
    "xfamily": [("gpt-5.5", "gpt-5.5", "g55"), ("gpt-5.4", "gpt-5.4", "exp"),
                ("gpt-5.1", "gpt-5.1", "exp"), ("gpt-5", "gpt-5", "exp"),
                ("gpt-4.1", "gpt-4.1", "g41"), ("gpt-oss-120b", "gpt-oss-120b", "exp"),
                ("grok-4.1", "grok-4-1-fast-non-reasoning", "exp"),
                ("DeepSeek-V4", "DeepSeek-V4-Pro", "exp")],
    "hidden": [("5.5-card", "gpt-5.5", "g55"), ("5.5-PDP", "gpt-5.5", "hid"),
               ("4.1-card", "gpt-4.1", "g41"), ("4.1-PDP", "gpt-4.1", "hid")],
}
PRODUCTS = ["laptop", "office_chair", "mattress", "backpack", "tent"]


def collect(globlist, model):
    P = defaultdict(list); tot = defaultdict(int); seen = set()
    for g in globlist:
        for sf in glob.glob(g + "/*/summary.json"):
            try:
                d = json.load(open(sf))
            except Exception:
                continue
            if d.get("model") != model:
                continue
            v = d.get("task_id", "").rsplit("-", 1)[-1]; c = d.get("condition")
            key = (d.get("seconds"), d.get("preservation"), v, c, sf)
            if key in seen:
                continue
            seen.add(key)
            tot[(v, c)] += 1
            p = d.get("preservation")
            if isinstance(p, (int, float)):
                P[(v, c)].append(p)
    return P, tot


def table(group, prefixes, title):
    mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
    print(f"\n#### {title}")
    print("| series | " + " | ".join(VARS) + " |")
    print("|" + "---|" * (len(VARS) + 1))
    for label, model, kind in GROUPS[group]:
        gl = sum((globs(p, kind) for p in prefixes), [])
        Pd, _ = collect(gl, model)
        cells = []
        for v in VARS:
            cl, cb = Pd.get((v, "clean"), []), Pd.get((v, "combined"), [])
            mc, mb = mean(cl), mean(cb)
            if cl and cb:
                cells.append(f"{mc:.2f}/{mb:.2f} (Δ{mc-mb:+.2f})")
            elif cl:
                cells.append(f"{mc:.2f}/– ")
            else:
                cells.append("–")
        print(f"| {label} | " + " | ".join(cells) + " |")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    groups = list(GROUPS) if which == "all" else [which]
    for g in groups:
        print(f"\n## {g.upper()}  (clean/combined (Δgap) per relativeness level 0→4)")
        for sid in PRODUCTS:
            table(g, [PREFIX[sid]], sid)
        table(g, [PREFIX[s] for s in PRODUCTS], "AGGREGATE (all 5 products)")


if __name__ == "__main__":
    main()
