"""Tasks = a natural-language instruction paired with structured user preferences.

The instruction is what the agent reads. The preferences are a small declarative
constraint set the *evaluator* uses to decide whether the agent stayed faithful to
the user — so you author the two together and they can't drift apart.

Preferences are a flat dict of constraints with suffix operators::

    preferences:
      price__max: 1000          # price <= 1000
      storage_gb__min: 512      # storage >= 512
      weight_kg__max: 1.45
      gaming: false             # exact equality
      amenities__contains: [Wifi, Pool]
      city__in: [Goa, Pune]

Operators: ``__min`` (>=), ``__max`` (<=), ``__lt``, ``__gt``, ``__le``, ``__ge``,
``__ne``, ``__in`` (value in list), ``__contains`` (attr — a str/list — contains the
value, or all values if a list); a bare key means equality. Each environment maps a
catalog item to a flat attribute dict, so the same DSL works everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_OPS = {
    "min": lambda a, b: a is not None and a >= b,
    "max": lambda a, b: a is not None and a <= b,
    "lt":  lambda a, b: a is not None and a < b,
    "gt":  lambda a, b: a is not None and a > b,
    "le":  lambda a, b: a is not None and a <= b,
    "ge":  lambda a, b: a is not None and a >= b,
    "ne":  lambda a, b: a != b,
    "in":  lambda a, b: a in b,
    "contains": lambda a, b: _contains(a, b),
}


def _contains(attr: Any, val: Any) -> bool:
    if attr is None:
        return False
    coll = attr if isinstance(attr, (list, tuple, set)) else [attr] if not isinstance(attr, str) else attr
    needles = val if isinstance(val, (list, tuple, set)) else [val]
    if isinstance(coll, str):
        return all(str(n).lower() in coll.lower() for n in needles)
    low = {str(x).lower() for x in coll}
    return all(str(n).lower() in low for n in needles)


def check_constraints(attrs: dict[str, Any], preferences: dict[str, Any]) -> list[str]:
    """Return the list of violated preference keys ([] = fully compliant)."""
    violations: list[str] = []
    for key, want in preferences.items():
        if "__" in key:
            field_name, op = key.rsplit("__", 1)
        else:
            field_name, op = key, "eq"
        have = attrs.get(field_name)
        ok = (have == want) if op == "eq" else _OPS.get(op, lambda a, b: True)(have, want)
        if not ok:
            violations.append(key)
    return violations


@dataclass
class TaskSpec:
    """One (instruction, preferences) test case for an environment."""

    task_id: str
    env: str
    instruction: str
    preferences: dict[str, Any] = field(default_factory=dict)
    catalog: Optional[str] = None        # named catalog the env should seed (None = default)
    condition: str = "clean"             # experiment condition (e.g. clean | steered)
    start_path: Optional[str] = None     # override the env's default start page
    params: dict[str, Any] = field(default_factory=dict)  # env-specific (e.g. booking dates)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_compliant(self, attrs: dict[str, Any]) -> bool:
        return not check_constraints(attrs, self.preferences)

    @classmethod
    def parse(cls, d: "dict | TaskSpec", env: str | None = None) -> "TaskSpec":
        if isinstance(d, TaskSpec):
            return d
        d = dict(d)
        return cls(task_id=d["task_id"], env=d.get("env") or env or "",
                   instruction=d["instruction"], preferences=d.get("preferences", {}),
                   catalog=d.get("catalog"), condition=d.get("condition", "clean"),
                   start_path=d.get("start_path"), params=d.get("params", {}),
                   metadata=d.get("metadata", {}))


def load_tasks(path: str | Path, env: str | None = None) -> list[TaskSpec]:
    """Load a YAML/JSON list of tasks (or a {tasks: [...], env: ...} mapping)."""
    import json
    p = Path(path)
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if isinstance(data, dict):
        env = data.get("env", env)
        data = data["tasks"]
    return [TaskSpec.parse(t, env=env) for t in data]
