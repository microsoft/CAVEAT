"""Derive the three preference variants from a scenario, plus rendering helpers used by
the instruction generator and faithfulness judge.
"""

from __future__ import annotations

from .schema import VARIANTS, GradedConstraint, PreferenceSpec, ScenarioSpec, ThresholdConstraint


def build_preferences(scenario: ScenarioSpec) -> dict[str, PreferenceSpec]:
    return {v: scenario.preference(v) for v in scenario.variants()}


# units that attach directly to the number (no space); everything else gets a space
_COMPACT_UNITS = {"GB", "TB", "MB", "kg", "g", "mm", "cm", "Hz", "Pa", "ml", "dB", "nits", "W", "%", "L", "°"}

# operator -> natural phrasing for rendering a constraint to text (for the LLM)
_OP_PHRASE = {
    "lt": "under", "le": "at most", "max": "at most",
    "gt": "over", "ge": "at least", "min": "at least",
    "ne": "not", "eq": "exactly",
}


def render_threshold(scenario: ScenarioSpec, t: ThresholdConstraint) -> str:
    if t.field == "no_addons":
        return ("buy it as a one-time purchase only — no protection plans, warranties, "
                "add-ons, or subscriptions")
    a = scenario.schema.by_key(t.field)
    label = a.label if a else t.field
    unit = (a.unit if a else "") or ""
    if a and a.kind == "bool":
        # boolean requirement, e.g. gaming=False, anc=True
        if t.value is True:
            return f"must have {label}"
        return f"must NOT be a {label}" if "laptop" in label or "gaming" in label else f"must not have {label}"
    if a and a.kind == "categorical":
        return f"{label} must be {t.value}"
    val = t.value
    money = unit == "$"
    if money:
        valstr = f"${val:g}"
    elif unit and unit != "$":
        # compact units attach directly (512GB, 0.95kg); word units take a space (275 lb, 3 years)
        sep = "" if unit in _COMPACT_UNITS else " "
        valstr = f"{val:g}{sep}{unit}"
    else:
        valstr = f"{val:g}"
    return f"{label} {_OP_PHRASE.get(t.op, t.op)} {valstr}"


def render_graded(scenario: ScenarioSpec, g: GradedConstraint) -> str:
    a = scenario.schema.by_key(g.attr)
    label = a.label if a else g.attr
    deg = {"slight": "somewhat ", "normal": "", "strong": "as much as possible — "}.get(g.degree, "")
    direction = "lower is better" if g.direction == "lower" else "higher is better"
    return f"{deg}prefer {label} where {direction}"


def render_preference(scenario: ScenarioSpec, pref: PreferenceSpec) -> dict:
    """A neutral, machine-and-human-readable rendering of a preference (for prompts)."""
    return {
        "noun": scenario.noun,
        "persona": scenario.persona,
        "thresholded_requirements": [render_threshold(scenario, t) for t in pref.thresholds],
        "graded_preferences": [render_graded(scenario, g) for g in pref.graded],
    }
