"""Turn generated artifacts into runnable objects: reconstruct an Amazon ``Catalog`` from
``catalog.json`` (so the existing adapter seeds/evaluates unchanged) and emit one
``TaskSpec`` per (scenario, variant). The condition (clean / 8 steering types) is supplied
by the experiment matrix, not baked into the task.

``register_all_generated()`` is called at Amazon-env import time so the generated catalogs
exist in every process — including the ``run_cell`` worker subprocesses, which look catalogs
up by name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core.task import TaskSpec
from ..envs.amazon.catalog import Catalog, Product
from . import serialize
from .schema import VARIANTS

_registered: set[str] = set()


def _product_from_seed(d: dict, *, preserve_stock: bool = False) -> Product:
    kwargs = dict(
        asin=d["asin"], title=d.get("title", d["asin"]), price=float(d.get("price", 0.0)),
        specs=dict(d.get("tech", {})), role=d.get("role", "distractor"),
        advertised=bool(d.get("advertised", False)), rating=float(d.get("rating", 4.5)),
        reviews=int(d.get("reviews", 100)), bought=int(d.get("bought", 500)),
        image=d.get("image", "laptop-generic.png"),
        list_price=d.get("list_price"), bullets=list(d.get("bullets", [])),
        description=d.get("description", ""),
        display_price=d.get("display_price"), true_price=d.get("true_price"),
        variants=list(d.get("variants", []) or []))
    # Runtime historically discarded authored stock.  Preserve it only for the
    # truthful successor, whose visible stock is a certified canonical fact; leaving
    # this argument absent keeps every earlier catalog's Product/to_seed path identical.
    if preserve_stock:
        kwargs["stock"] = int(d.get("stock", 100))
    return Product(**kwargs)


def build_catalog(scenario_id: str, root: Optional[Path] = None) -> Catalog:
    cj = serialize.load_catalog_json(scenario_id, root)
    serving = cj.get("serving") or {}
    truthful = bool(isinstance(serving, dict) and serving.get("truthful"))
    products = [
        _product_from_seed(p, preserve_stock=truthful)
        for p in cj.get("products", [])
    ]
    # `serving` (hard tier only) rides through unchanged so the runtime Catalog — and the
    # _catalogs/*.json the server actually reads — carries the same object the generator
    # wrote. Absent for every original scenario => {} => Catalog.to_seed_json() omits it.
    return Catalog(name=scenario_id, products=products,
                   category_slug=cj.get("category_slug", "laptops"),
                   bury_index=int(cj.get("bury_index", 6)),
                   serving=serving if isinstance(serving, dict) else {})


def register_catalog(scenario_id: str, root: Optional[Path] = None) -> Catalog:
    from ..envs.amazon.catalog import CATALOGS
    cat = build_catalog(scenario_id, root)
    CATALOGS[scenario_id] = cat
    _registered.add(scenario_id)
    return cat


def register_all_generated(root: Optional[Path] = None) -> list[str]:
    base = Path(root) if root else serialize.DATA_ROOT
    if not base.exists():
        return []
    done = []
    for d in sorted(base.iterdir()):
        if (d / "catalog.json").exists():
            try:
                register_catalog(d.name, root)
                done.append(d.name)
            except Exception as e:  # noqa: BLE001
                print(f"[benchmark] failed to register catalog {d.name}: {e}")
    return done


def benchmark_tasks(scenario_id: str, root: Optional[Path] = None,
                    variants=VARIANTS) -> list[TaskSpec]:
    """One TaskSpec per (scenario, variant). Condition is applied by the experiment."""
    register_catalog(scenario_id, root)
    prefs = serialize.load_preferences(scenario_id, root)
    insts = serialize.load_instructions(scenario_id, root)
    meta = serialize.load_meta(scenario_id, root)
    tasks = []
    for v in variants:
        pref = prefs[v]
        gi = insts[v]
        tasks.append(TaskSpec(
            task_id=f"{scenario_id}-{v}", env="amazon", catalog=scenario_id,
            instruction=gi.text, preferences=pref.dsl(),
            metadata={"scenario_id": scenario_id, "variant": v,
                      "graded": pref.graded_map(),
                      "instruction_status": gi.status,
                      "schema_version": meta.get("schema_version"),
                      "seed": meta.get("seed")}))
    return tasks


def all_tasks(root: Optional[Path] = None, variants=VARIANTS) -> list[TaskSpec]:
    base = Path(root) if root else serialize.DATA_ROOT
    out: list[TaskSpec] = []
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        if (d / "catalog.json").exists():
            out.extend(benchmark_tasks(d.name, root, variants))
    return out
