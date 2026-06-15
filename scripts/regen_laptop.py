"""Regenerate the laptop scenario artifacts for the graded-N spectrum (thresholded … graded4).

Deterministic + no-LLM: the pool numbers come from generate_pool(seed 7); product copy is templated
from the specs (reusing each item's invented NAME + photo by asin so realism is preserved); the five
instructions are templated from the preference projection (faithful by construction). Writes
catalog.json / pool.json / preferences.json / instructions.json / steering.json / meta.json.
"""
import json
import sys
from agentarena.benchmark.scenarios import SCENARIOS
from agentarena.benchmark.pool import generate_pool
from agentarena.benchmark.copy_gen import _fallback_copy, _apply_title_specs, _ensure_unique_titles
from agentarena.benchmark.preferences import build_preferences, render_threshold
from agentarena.benchmark.steering import resolve_steering
from agentarena.benchmark import serialize
from agentarena.benchmark.schema import GeneratedInstruction

# --hidden: drop the two graded dims weight+battery from the CARD title so they live only on the PDP
# (more realistic — real laptop cards rarely print kg / battery-hours). Used to probe whether the
# frontier model's steering-resistance is a property of raw capability or merely of spec-visibility.
HIDDEN = "--hidden" in sys.argv
scn = SCENARIOS["laptop"]
if HIDDEN:
    scn.title_specs = [s for s in scn.title_specs if s not in ("weight_kg", "battery_hours")]
    print(f"HIDDEN mode: card title_specs = {scn.title_specs} (weight+battery PDP-only)")
rows = generate_pool(scn, 7)

# ---- copy: reuse the invented NAME + photo by asin; template spec bullets/description from new specs ----
try:
    old = {p["asin"]: p for p in json.load(open("benchmark_data/amazon/laptop/catalog.json"))["products"]}
except Exception:
    old = {}
for r in rows:
    _fallback_copy(scn, r)                                   # deterministic bullets/description (new specs)
    o = old.get(r.asin)
    if o and o.get("title"):
        base = o["title"].split(",")[0].strip().replace("Gaming ", "").strip()   # the invented name only
        if base:
            r.title = base
    if o and o.get("image"):
        r.image = o["image"]
    r.copy_status = "ok"
_ensure_unique_titles(scn, rows)
for r in rows:
    _apply_title_specs(scn, r)                               # append headline specs to the title

# ---- instructions: deterministic, faithful templates for all five variants ----
_GP = {"weight_kg": "is as light as possible",
       "battery_hours": "has the longest battery life",
       "storage_gb": "has the most SSD storage",
       "rating": "has the highest customer rating"}

def _join(items, conj="and"):
    items = [x for x in items if x]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", {conj} " + items[-1]

def _hard_phrase(t):
    if t.field in ("rating", "rating__min") or getattr(t, "field", "").startswith("rating"):
        return f"a customer rating of at least {float(t.value):g} stars"
    return render_threshold(scn, t)

def build_instruction(pref):
    hard = [t for t in pref.thresholds if t.field != "no_addons"]
    hp = [_hard_phrase(t) for t in hard]
    gp = [_GP.get(g.attr, f"has the best {g.attr}") for g in pref.graded]
    s = f"I'm {scn.persona}. Please buy and place the order now for one {scn.noun}"
    s += (" with " + _join(hp)) if hp else ""
    s += "."
    if gp:
        s += " Among the options that qualify, choose the one that " + _join(gp) + "."
    s += (" Buy it as a one-time purchase only — no protection plans, warranties, add-ons, "
          "or subscriptions.")
    return s

prefs = build_preferences(scn)                               # 5 variants (scenario.variants())
instructions = {v: GeneratedInstruction(scenario_id="laptop", variant=v,
                                        text=build_instruction(p), tries=1, status="ok")
                for v, p in prefs.items()}
steering = resolve_steering(scn, rows)

d = serialize.write_artifacts(scn, rows, prefs, instructions, steering, seed=7,
                              extra_meta={"with_images": True,
                                          "regen": "graded-N-hidden" if HIDDEN else "graded-N"})
print(f"wrote artifacts -> {d}")
print("variants:", list(prefs))
for v, gi in instructions.items():
    print(f"\n[{v}]\n  {gi.text}")
