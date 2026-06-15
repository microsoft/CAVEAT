"""6-block table for the config-drip laptop×combined run (cfg18): [gpt-4.1,gpt-5.5] ×
[thresholded,mixed,graded], 3 reps each. Reports the UNIFIED continuous preservation P
(price treated like any spec; graded = most-punishing vs the faithful) using the actually
purchased storage configuration (config-drip aware), plus what each rep bought.

Run: python scripts/cfg18_report.py
"""
import glob
import json
import os

from agentarena.benchmark import scenarios as S
from agentarena.benchmark.serialize import load_pool
from agentarena.scoring.basket import chosen_attrs
from agentarena.scoring.continuous import preservation

SC = "laptop"
rows = {r.asin: r for r in load_pool(SC)}
cands = [{**r.attrs(), "no_addons": True} for r in rows.values()]
spec = S.get(SC)
MODELS = ["gpt-4.1", "gpt-5.5"]
VARIANTS = ["thresholded", "mixed", "graded"]


def cell_result(model, variant):
    """Return list of per-rep dicts for laptop-<variant>-combined under <model>."""
    out = []
    pat = f"results/cfg18_r*/amazon__browseruse__{model}__{SC}-{variant}__combined/trajectory.json"
    for tj in sorted(glob.glob(pat)):
        try:
            d = json.load(open(tj))
        except Exception:
            continue
        ev = d.get("evaluation") or {}
        det = ev.get("details") or {}
        chosen = ev.get("chosen")
        outcome = ev.get("outcome")
        rep = os.path.dirname(tj).split("/")[1]   # cfg18_rN
        if outcome in ("none", "error", "skipped") or chosen is None:
            out.append({"rep": rep, "P": None, "chosen": None, "cfg": None,
                        "role": None, "outcome": outcome})
            continue
        r = rows.get(chosen)
        li = (det.get("basket") or {}).get("line_items") or []
        cfg = next((it.get("variant_value") for it in li if it.get("asin") == chosen), None)
        if r is None:                       # off-catalog
            out.append({"rep": rep, "P": 0.0, "chosen": chosen, "cfg": cfg,
                        "role": "off_catalog", "outcome": outcome})
            continue
        a = chosen_attrs(r.attrs(), det, r.asin, variants=getattr(r, "variants", None))
        a.setdefault("no_addons", True)
        pref = spec.preference(variant)
        P = preservation(a, pref.dsl(), pref.graded_map(), cands, variant=variant)
        role = r.role if r.role != "satisfice" else (r.decoy_kind or "satisfice")
        if r.role == "decoy":
            role = f"decoy_{r.decoy_kind}"
        out.append({"rep": rep, "P": round(P, 3), "chosen": chosen, "cfg": cfg,
                    "role": role, "outcome": outcome,
                    "paid": det.get("price_paid"), "storage": a.get("storage_gb")})
    return out


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


print("# Config-drip — laptop × combined steering — preservation P (unified scoring)\n")
print("P = unified preservation (binary thresholds incl. price; graded = most-punishing vs the")
print("faithful; flat mean over specs). Scored on the PURCHASED storage configuration.\n")

# compact matrix first
print("| variant | gpt-4.1 (3 reps) | mean | gpt-5.5 (3 reps) | mean |")
print("|---|---|---|---|---|")
detail = {}
for variant in VARIANTS:
    cells41 = cell_result("gpt-4.1", variant)
    cells55 = cell_result("gpt-5.5", variant)
    detail[variant] = (cells41, cells55)
    def fmt(cells):
        return ", ".join(f"{c['P'] if c['P'] is not None else 'none'}" for c in cells)
    m41 = mean([c["P"] for c in cells41])
    m55 = mean([c["P"] for c in cells55])
    print(f"| {variant} | {fmt(cells41)} | {m41:.3f} | {fmt(cells55)} | {m55:.3f} |")

# the 6 blocks (what each rep bought)
print("\n## The 6 blocks (what each rep purchased)\n")
for model in MODELS:
    for variant in VARIANTS:
        cells = detail[variant][0 if model == "gpt-4.1" else 1]
        m = mean([c["P"] for c in cells])
        print(f"### {model} · {variant}   (mean P = {m:.3f})")
        for c in cells:
            if c["P"] is None:
                print(f"  - {c['rep']}: NONE ({c['outcome']}) — no purchase")
            else:
                cfgs = f" [{c['cfg']}]" if c.get("cfg") else ""
                paid = f" ${c['paid']:.0f}" if c.get("paid") else ""
                st = f" {int(c['storage'])}GB" if c.get("storage") else ""
                print(f"  - {c['rep']}: P={c['P']:.3f}  {c['chosen']}{cfgs}{paid}{st}  ({c['role']})")
        print()
