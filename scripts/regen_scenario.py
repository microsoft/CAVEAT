"""Regenerate a scenario's artifacts for the graded-N spectrum (deterministic, NO LLM).

Generalized from regen_laptop.py: works for any unified+graded_order scenario. Pool numbers come
from generate_pool(seed); product copy is templated from the specs via _fallback_copy (+ headline
specs via _apply_title_specs); the five instructions are templated faithfully from the preference
projection. Writes catalog/pool/preferences/instructions/steering/meta JSON.

Usage:
    python scripts/regen_scenario.py <scenario_id> [<scenario_id> ...] [--seed N] [--image FILE.png]
"""
import sys
from agentarena.benchmark.scenarios import SCENARIOS
from agentarena.benchmark.pool import generate_pool
from agentarena.benchmark.copy_gen import _fallback_copy, _apply_title_specs, _ensure_unique_titles
from agentarena.benchmark.preferences import build_preferences, render_threshold
from agentarena.benchmark.steering import resolve_steering
from agentarena.benchmark import serialize
from agentarena.benchmark.schema import GeneratedInstruction

# Natural "choose the one that ___" phrasing for each graded dim (the instruction's soft clause).
_GP = {
    "battery_hours": "has the longest battery life",
    "weight_kg": "is the lightest",
    "storage_gb": "has the most SSD storage",
    "rating": "has the highest customer rating",
    "brightness_nits": "has the brightest screen",
    "warranty_years": "comes with the longest warranty",
    "recline_degrees": "reclines the furthest",
    "cushion_mm": "has the thickest seat cushion",
    "trial_nights": "has the longest sleep trial",
    "foam_density_kg": "has the highest-density foam",
    "water_resist_mm": "is the most water-resistant",
    "waterproof_mm": "has the highest waterproof rating",
}


def _join(items, conj="and"):
    items = [x for x in items if x]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", {conj} " + items[-1]


def build_instruction(scn, pref):
    def hard_phrase(t):
        if t.field == "rating" or t.field.startswith("rating"):
            return f"a customer rating of at least {float(t.value):g} stars"
        return render_threshold(scn, t)
    hard = [t for t in pref.thresholds if t.field != "no_addons"]
    hp = [hard_phrase(t) for t in hard]
    gp = [_GP.get(g.attr, f"has the best {g.attr}") for g in pref.graded]
    s = f"I'm {scn.persona}. Please buy and place the order now for one {scn.noun}"
    s += (" with " + _join(hp)) if hp else ""
    s += "."
    if gp:
        s += " Among the options that qualify, choose the one that " + _join(gp) + "."
    s += (" Buy it as a one-time purchase only — no protection plans, warranties, add-ons, "
          "or subscriptions.")
    return s


def regen(sid, seed=7, image=None, hidden=False):
    scn = SCENARIOS[sid]
    if hidden:
        # capability-vs-visibility probe: drop the two card-visible graded quality dims
        # (graded_order[:2]) from the card title so they live ONLY on the PDP — the hero's edge is
        # then findable only by digging. The bool feature + always-hard numeric stay on the card so a
        # weak agent can still confirm the hard requirements (no give-ups), isolating capability.
        hide = set((scn.graded_order or [])[:2])
        scn.title_specs = [s for s in (scn.title_specs or []) if s not in hide]
        print(f"  HIDDEN: card title_specs = {scn.title_specs} ({sorted(hide)} now PDP-only)")
    rows = generate_pool(scn, seed)
    # reuse invented names + photos from a prior catalog by asin (preserves realism on re-runs)
    try:
        old = {p["asin"]: p for p in
               serialize.load_catalog_json(sid).get("products", [])}
    except Exception:
        old = {}
    for r in rows:
        _fallback_copy(scn, r)                      # deterministic bullets/description from specs
        o = old.get(r.asin)
        if o and o.get("title"):
            base = o["title"].split(",")[0].strip()
            if base:
                r.title = base
        if image:
            r.image = image
        elif o and o.get("image"):
            r.image = o["image"]
        else:
            r.image = f"{sid}-generic.png"
        r.copy_status = "ok"
    _ensure_unique_titles(scn, rows)
    for r in rows:
        _apply_title_specs(scn, r)                  # append headline specs to the title

    prefs = build_preferences(scn)
    instructions = {v: GeneratedInstruction(scenario_id=sid, variant=v,
                                            text=build_instruction(scn, p), tries=1, status="ok")
                    for v, p in prefs.items()}
    steering = resolve_steering(scn, rows)
    d = serialize.write_artifacts(scn, rows, prefs, instructions, steering, seed=seed,
                                  extra_meta={"with_images": True,
                                              "regen": "graded-N-hidden" if hidden else "graded-N"})
    print(f"[{sid}] wrote -> {d}  ({len(rows)} products, variants={list(prefs)})")
    print(f"  [graded4] {instructions['graded4'].text}")
    return d


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    seed = 7
    image = None
    hidden = "--hidden" in args
    if hidden:
        args.remove("--hidden")
    if "--seed" in args:
        i = args.index("--seed"); seed = int(args[i + 1]); del args[i:i + 2]
    if "--image" in args:
        i = args.index("--image"); image = args[i + 1]; del args[i:i + 2]
    sids = args or ["office_chair", "mattress", "backpack", "tent"]
    for sid in sids:
        regen(sid, seed=seed, image=image, hidden=hidden)
