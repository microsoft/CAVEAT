"""Post-run fold for the mech8 clean add-one taxonomy ablation (results/mech8_v1):
write strict-family keys into each summary, rebuild benchmark_data/reports/mech8_data.json,
and print the per-(model, condition) ladder with the mm_v1 clean/combined anchors."""
import glob
import json
import sys
from collections import Counter, defaultdict

sys.path.insert(0, __file__.rsplit("scripts/", 1)[0] or ".")  # repo root (portable: works from any checkout)
from agentarena.scoring.rescore import cell_strict  # noqa: E402

FIELDS = ("model", "scaffold", "condition", "task_id", "outcome", "num_steps", "preservation_strict")
OUT = "benchmark_data/reports/mech8_data.json"
ORDER = ["only-shelves", "only-pin", "only-sponsored", "only-ranking", "only-promo",
         "only-trust", "only-scarcity", "only-drip", "only-addon", "only-friction",
         "org-promo", "org-trust", "org-scarcity"]

recs = []
for tj in sorted(glob.glob("results/mech8_v1/mech8_r*/amazon__browseruse__*__*/trajectory.json")):
    sf = tj[: -len("trajectory.json")] + "summary.json"
    s = json.load(open(sf))
    s["preservation_strict"] = cell_strict(tj, soft=False)
    s["preservation_cont"] = cell_strict(tj, soft=True)
    json.dump(s, open(sf, "w"), indent=2, default=str)
    recs.append({k: s.get(k) for k in FIELDS})

json.dump(recs, open(OUT, "w"))
print(f"{OUT}: {len(recs)} records")

# mm_v1 anchors (same models/scaffold/budget, graded4): clean + combined
anchor = defaultdict(list)
try:
    for r in json.load(open("benchmark_data/reports/figure_data.json")):
        if (r.get("scaffold") == "browseruse" and r.get("condition") in ("clean", "combined")
                and str(r.get("task_id", "")).endswith("graded4")):
            anchor[(r["model"], r["condition"])].append(r.get("preservation_strict"))
except Exception as e:  # noqa: BLE001
    print(f"(anchors unavailable: {e})")

agg = defaultdict(list)
for r in recs:
    agg[(r["model"], r["condition"])].append(r)
models = sorted({m for m, _ in agg})
for m in models:
    print(f"\n--- {m} (graded4, valid-only strict P*) ---")
    for c, tag in [("clean", "mm_v1 anchor"), ("combined", "mm_v1 anchor")]:
        vals = [v for v in anchor.get((m, c), []) if v is not None]
        if vals:
            print(f"  {c:15s} P*={sum(vals)/len(vals):.3f} n={len(vals):2d}  [{tag}]")
    for c in ORDER:
        rs = agg.get((m, c), [])
        if not rs:
            continue
        ok = [r["preservation_strict"] for r in rs
              if r["outcome"] != "none" and r["preservation_strict"] is not None]
        nones = sum(1 for r in rs if r["outcome"] == "none")
        line = (f"  {c:15s} P*={sum(ok)/len(ok):.3f}" if ok else f"  {c:15s} (no valid)")
        print(f"{line} n={len(rs):2d} nones={nones} {dict(Counter(r['outcome'] for r in rs))}")
