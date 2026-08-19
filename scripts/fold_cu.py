"""Post-run fold for the computer-use (websurfer) experiment (results/cu_v1): write
strict-family keys into each summary, then REBUILD benchmark_data/reports/cu_data.json from
scratch (separate snapshot from figure_data.json — different harness protocol: websurfer
scaffold, 1 atomic action per step, max-steps 75)."""
import glob
import json
import sys
from collections import Counter, defaultdict

sys.path.insert(0, __file__.rsplit("scripts/", 1)[0] or ".")  # repo root (portable: works from any checkout)
from agentarena.scoring.rescore import cell_strict  # noqa: E402

FIELDS = ("model", "scaffold", "condition", "task_id", "outcome", "num_steps", "preservation_strict")
OUT = "benchmark_data/reports/cu_data.json"

recs = []
for tj in sorted(glob.glob("results/cu_v1/cu_r*/amazon__websurfer__*__*/trajectory.json")):
    sf = tj[: -len("trajectory.json")] + "summary.json"
    s = json.load(open(sf))
    s["preservation_strict"] = cell_strict(tj, soft=False)
    s["preservation_cont"] = cell_strict(tj, soft=True)
    json.dump(s, open(sf, "w"), indent=2, default=str)
    recs.append({k: s.get(k) for k in FIELDS})

json.dump(recs, open(OUT, "w"))
print(f"{OUT}: {len(recs)} records")
agg = defaultdict(list)
for r in recs:
    agg[(r["model"], r["condition"])].append(r)
for (m, c), rs in sorted(agg.items()):
    ok = [r["preservation_strict"] for r in rs if r["outcome"] != "none" and r["preservation_strict"] is not None]
    print(f"{m:18s} {c:26s} n={len(rs):2d} nones={sum(1 for r in rs if r['outcome']=='none')} "
          f"P*={sum(ok)/len(ok):.3f}" if ok else f"{m:18s} {c:26s} n={len(rs)} (no valid)",
          dict(Counter(r["outcome"] for r in rs)))
