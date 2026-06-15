"""Schemas for the marketplace-steering benchmark generator.

Everything the pipeline produces is described by the dataclasses here. The guiding
split is the **determinism boundary**:

* The *scientific core* — attribute schemas, the numeric product pool, the ground-truth
  preferences, and the steering resolution — is authored or produced by a seeded RNG and
  is fully reproducible / version-controlled.
* The LLM only adds *dressing* (titles/bullets/descriptions, the natural-language
  instruction, images) and is validated to never contradict a scored number.

The serialized JSON for a product (``ProductRow.to_seed_dict``) is shaped exactly like
``agentarena.envs.amazon.catalog.Product.to_seed`` so the existing Amazon server seeds
from it unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

SCHEMA_VERSION = 1

# Direction of "better" for a numeric attribute / graded preference.
LOWER = "lower"
HIGHER = "higher"


# --------------------------------------------------------------------------- #
# Attribute schema (per scenario)
# --------------------------------------------------------------------------- #
@dataclass
class AttributeDef:
    """One product attribute. ``key`` must match the preference-DSL field name."""

    key: str
    label: str                       # human label for copy/instruction ("weight")
    unit: str = ""                   # "kg", "GB", "hours", "$", "" ...
    kind: str = "numeric"            # "numeric" | "bool" | "categorical"
    better: Optional[str] = None     # LOWER | HIGHER | None (no natural direction)
    band_low: Optional[float] = None
    band_high: Optional[float] = None
    step: Optional[float] = None     # quantisation grid (e.g. storage in {256,512,1024})
    choices: Optional[list] = None   # for bool/categorical
    visibility: str = "card"         # "card" (shown in listing) | "detail" (PDP only)
    display_in_specs: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AttributeDef":
        return cls(**d)


@dataclass
class AttributeSchema:
    scenario_id: str
    category_slug: str
    attributes: list[AttributeDef]
    price_attr: str = "price"

    def by_key(self, key: str) -> Optional[AttributeDef]:
        return next((a for a in self.attributes if a.key == key), None)

    def to_dict(self) -> dict:
        return {"scenario_id": self.scenario_id, "category_slug": self.category_slug,
                "price_attr": self.price_attr,
                "attributes": [a.to_dict() for a in self.attributes]}

    @classmethod
    def from_dict(cls, d: dict) -> "AttributeSchema":
        return cls(scenario_id=d["scenario_id"], category_slug=d["category_slug"],
                   price_attr=d.get("price_attr", "price"),
                   attributes=[AttributeDef.from_dict(a) for a in d["attributes"]])


# --------------------------------------------------------------------------- #
# Preferences (ground truth)
# --------------------------------------------------------------------------- #
@dataclass
class ThresholdConstraint:
    """A thresholded/equality constraint, expressed in the existing DSL key form.

    ``key`` carries the operator suffix, e.g. ``price__lt`` / ``storage_gb__min`` /
    ``gaming`` (bare key == equality).
    """

    key: str
    value: Any
    cls: str = "thresholded"

    @property
    def field(self) -> str:
        return self.key.rsplit("__", 1)[0] if "__" in self.key else self.key

    @property
    def op(self) -> str:
        return self.key.rsplit("__", 1)[1] if "__" in self.key else "eq"

    def to_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "cls": self.cls}

    @classmethod
    def from_dict(cls, d: dict) -> "ThresholdConstraint":
        return cls(key=d["key"], value=d["value"], cls=d.get("cls", "thresholded"))


@dataclass
class GradedConstraint:
    """A degree preference over a numeric attribute. Carries the underlying requirement cut
    (``value``) so the graded scorer can treat it as a SOFT requirement: meeting the cut scores
    1.0, missing it scores a requirement-relative proportional penalty (no catalog dependence,
    so the 'faithful' need not be the catalog extreme)."""

    attr: str
    direction: str                   # LOWER | HIGHER
    cls: str = "graded"
    degree: str = "normal"           # "slight" | "normal" | "strong" (phrasing/weight)
    value: Any = None                # the requirement cut (e.g. 512, 1.45) — soft-scoring anchor

    def to_dict(self) -> dict:
        return {"attr": self.attr, "direction": self.direction, "cls": self.cls,
                "degree": self.degree, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict) -> "GradedConstraint":
        return cls(attr=d["attr"], direction=d["direction"], cls=d.get("cls", "graded"),
                   degree=d.get("degree", "normal"), value=d.get("value"))


@dataclass
class PreferenceAttr:
    """One numeric preference dimension carrying BOTH facets — a hard threshold (op,value)
    and a graded direction. ``ScenarioSpec.preference(variant)`` projects it to a
    ThresholdConstraint or a GradedConstraint per variant, so all three variants encode the
    SAME underlying preference at increasing 'gradedness' (thresholded = all cutoffs →
    mixed = some cutoffs/some degrees → graded = all degrees)."""

    attr: str                        # bare field key, e.g. "price", "storage_gb"
    op: str                          # threshold operator: lt|le|max|gt|ge|min
    value: Any                       # threshold cut value
    direction: str                   # LOWER | HIGHER (graded facet)
    degree: str = "normal"           # graded phrasing/weight: slight|normal|strong
    always_hard: bool = False        # if True, stays a HARD threshold in EVERY variant (never a
                                     # degree) — e.g. the price BUDGET: a hard cap in graded too, so
                                     # the faithful (best on the soft specs) need not be the cheapest.

    @property
    def threshold_key(self) -> str:
        return f"{self.attr}__{self.op}"

    def as_threshold(self) -> "ThresholdConstraint":
        return ThresholdConstraint(key=self.threshold_key, value=self.value)

    def as_graded(self) -> "GradedConstraint":
        return GradedConstraint(attr=self.attr, direction=self.direction, degree=self.degree,
                                value=self.value)

    def to_dict(self) -> dict:
        return {"attr": self.attr, "op": self.op, "value": self.value,
                "direction": self.direction, "degree": self.degree, "always_hard": self.always_hard}

    @classmethod
    def from_dict(cls, d: dict) -> "PreferenceAttr":
        return cls(attr=d["attr"], op=d["op"], value=d["value"],
                   direction=d["direction"], degree=d.get("degree", "normal"),
                   always_hard=d.get("always_hard", False))


@dataclass
class PreferenceSpec:
    scenario_id: str
    variant: str                     # "thresholded" | "graded" | "mixed"
    thresholds: list[ThresholdConstraint] = field(default_factory=list)
    graded: list[GradedConstraint] = field(default_factory=list)

    def dsl(self) -> dict[str, Any]:
        """The thresholded half as the flat dict consumed by ``check_constraints``."""
        return {t.key: t.value for t in self.thresholds}

    def graded_map(self) -> dict[str, tuple]:
        """The graded half as ``{attr: (direction, requirement_value)}`` — the scorer uses the cut
        to soft-score (meet -> 1.0, miss -> proportional). (Lives in TaskSpec.metadata.)"""
        return {g.attr: (g.direction, g.value) for g in self.graded}

    def to_dict(self) -> dict:
        return {"scenario_id": self.scenario_id, "variant": self.variant,
                "thresholds": [t.to_dict() for t in self.thresholds],
                "graded": [g.to_dict() for g in self.graded]}

    @classmethod
    def from_dict(cls, d: dict) -> "PreferenceSpec":
        return cls(scenario_id=d["scenario_id"], variant=d["variant"],
                   thresholds=[ThresholdConstraint.from_dict(t) for t in d.get("thresholds", [])],
                   graded=[GradedConstraint.from_dict(g) for g in d.get("graded", [])])


# --------------------------------------------------------------------------- #
# Product row (numeric core + LLM copy)
# --------------------------------------------------------------------------- #
@dataclass
class ProductRow:
    """One catalog product. ``specs``/``price`` are the deterministic, honest, scored
    values. Steering manipulations (drip fee, inflated reviews, ...) are NOT baked here
    — they are resolved per-condition in ``steering.py`` and applied at seed/serve time.
    """

    asin: str
    role: str                        # "compliant" | "decoy" | "distractor"
    advertised: bool
    specs: dict[str, Any]            # numeric/bool/categorical attrs (no price)
    price: float                     # honest, true price (what scoring uses for the item)
    list_price: float
    rating: float
    reviews: int
    bought: int
    stock: int
    fail_reasons: list[str] = field(default_factory=list)   # audit: which constraints it fails
    decoy_kind: str = ""           # "" | "spec" | "cost" | "config" (PDP storage-config drip)
    # PDP-only storage configurations (the "config-drip"): each = {storage_gb, price, label}.
    # The product's own price/specs == the BASE (cheapest, lowest-storage) config shown on the
    # card; the pricier higher-storage configs live here and are visible only on the detail page.
    # Empty -> a flat product (single configuration). Scoring uses whichever config was purchased.
    variants: list = field(default_factory=list)
    image: str = ""
    image_tier: str = "stock"        # "hero" (gpt-image-1) | "stock" (reused)
    # ---- LLM-filled (validated to not contradict specs/price) ----
    title: str = ""
    bullets: list[str] = field(default_factory=list)
    description: str = ""
    copy_status: str = "pending"     # "ok" | "flagged" | "pending"

    def attrs(self) -> dict[str, Any]:
        """Flat attribute dict for preference checking. ``rating`` is included so it can serve as a
        graded preference dimension (higher = better) — the faithful is the genuine top-rated item,
        a satisficing trap sits at the rating floor (and under steering its DISPLAY is inflated)."""
        return {**self.specs, "price": self.price, "rating": self.rating}

    def to_seed_dict(self) -> dict[str, Any]:
        """Shaped like Product.to_seed() so the Amazon server seeds from it unchanged.

        ``tech`` carries the raw numeric specs (also used by the on-page technical
        details). ``display_price``/``true_price`` are left None here — the steered
        condition sets the drip at seed time from the resolved steering spec.
        """
        tech = dict(self.specs)
        return {
            "asin": self.asin, "title": self.title, "price": self.price,
            "list_price": self.list_price, "rating": self.rating,
            "reviews": self.reviews, "bought": self.bought,
            "image": self.image or "laptop-generic.png",
            "bullets": self.bullets, "description": self.description, "tech": tech,
            "role": self.role, "advertised": self.advertised,
            "stock": self.stock, "decoy_kind": self.decoy_kind,
            "variants": self.variants,
            "display_price": None, "true_price": None,
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProductRow":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# --------------------------------------------------------------------------- #
# Steering (resolved per scenario)
# --------------------------------------------------------------------------- #
@dataclass
class SteeringSpec:
    """One of the 8 steering types, resolved to server-applicable knobs for a scenario.

    Written to ``steering.json`` and handed to the Amazon server via the
    ``AMAZON_STEERING`` env var. The clean baseline is the absence of any of these.
    """

    steering_id: str                 # sponsored|ranking|drip|promo|addon|scarcity|trust|friction
    taxonomy_ref: str                # section in marketplace_steering_taxonomy.md
    decoy_skus: list[str] = field(default_factory=list)
    bury_skus: list[str] = field(default_factory=list)
    bury_index: int = 6
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SteeringSpec":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# --------------------------------------------------------------------------- #
# Instruction + faithfulness
# --------------------------------------------------------------------------- #
@dataclass
class ConstraintCheck:
    ref: str
    present: bool
    distorted: bool
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FaithfulnessReport:
    completeness_ok: bool
    no_leakage_ok: bool
    no_distortion_ok: bool
    naturalness_ok: bool
    per_constraint: list[ConstraintCheck] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    judge_models: list[str] = field(default_factory=list)
    verdict: str = "fail"            # "pass" | "fail"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FaithfulnessReport":
        pc = [ConstraintCheck(**c) for c in d.get("per_constraint", [])]
        return cls(completeness_ok=d["completeness_ok"], no_leakage_ok=d["no_leakage_ok"],
                   no_distortion_ok=d["no_distortion_ok"], naturalness_ok=d["naturalness_ok"],
                   per_constraint=pc, flags=d.get("flags", []),
                   judge_models=d.get("judge_models", []), verdict=d.get("verdict", "fail"))


@dataclass
class GeneratedInstruction:
    scenario_id: str
    variant: str
    text: str
    tries: int = 0
    report: Optional[FaithfulnessReport] = None
    status: str = "pending"          # "ok" | "flagged_after_max_tries" | "pending"

    def to_dict(self) -> dict:
        return {"scenario_id": self.scenario_id, "variant": self.variant, "text": self.text,
                "tries": self.tries, "status": self.status,
                "report": self.report.to_dict() if self.report else None}

    @classmethod
    def from_dict(cls, d: dict) -> "GeneratedInstruction":
        rep = FaithfulnessReport.from_dict(d["report"]) if d.get("report") else None
        return cls(scenario_id=d["scenario_id"], variant=d["variant"], text=d["text"],
                   tries=d.get("tries", 0), report=rep, status=d.get("status", "pending"))


# --------------------------------------------------------------------------- #
# Scenario design input (authored, deterministic source of truth)
# --------------------------------------------------------------------------- #
@dataclass
class ScenarioSpec:
    scenario_id: str
    category_slug: str
    noun: str                        # "laptop" — for copy/instruction
    persona: str                     # "a university student" — for instruction realism
    schema: AttributeSchema
    # ---- legacy explicit constraint lists (used by un-migrated scenarios) ----
    thresholds: list[ThresholdConstraint] = field(default_factory=list)   # full hard set
    graded: list[GradedConstraint] = field(default_factory=list)          # numeric directions
    mixed_threshold_keys: list[str] = field(default_factory=list)         # subset for mixed
    mixed_graded_attrs: list[str] = field(default_factory=list)           # subset for mixed
    trap_threshold_keys: list[str] = field(default_factory=list)          # keys the decoy violates
    appeal_note: str = ""                    # what makes the decoy superficially attractive
    n_compliant: int = 3
    n_decoy: int = 2
    n_distractor: int = 25
    bury_index: int = 6
    # Per-attribute tier override for the COST decoy's graded specs, e.g.
    # {"weight_kg": "good", "battery_hours": "bad"}. Default None = uniform "midbad" on every
    # graded dim. Set this to build a HOLISTIC trap: a decoy that WINS one graded dimension
    # (near-best) but LOSES another (near-worst), so a multi-attribute preference is preserved
    # only by an agent that balances both dims rather than anchoring on the salient spec. The
    # hero still strictly dominates every dim, so P_oracle stays 1.
    cost_decoy_graded_tiers: Optional[dict] = None
    # ---- unified preference (Option A): the SAME numeric attrs expressed at increasing
    # 'gradedness' across the three variants. When `preference_attrs` is set, preference() and
    # the pool source from these instead of the legacy lists; default None keeps un-migrated
    # scenarios byte-identical. ----
    preference_attrs: Optional[list] = None      # list[PreferenceAttr]: numeric dims, both facets
    bool_constraints: Optional[list] = None      # list[ThresholdConstraint]: always-hard (gaming, no_addons)
    mixed_graded_attr_set: Optional[list] = None  # which preference_attrs go graded in 'mixed'
    # ---- generalized graded-N spectrum (graded3 / graded4 / …) ----
    # Ordered SOFT preference_attr names in the order they soften into degrees as the instruction gets
    # more 'relative': `mixed` grades the first 1, `graded` the first 2, `graded3` the first 3,
    # `graded4` the first 4. always_hard attrs (the budget) never appear here. When set, preference()
    # projects via this ordered prefix (superseding mixed_graded_attr_set); None => legacy 3-variant.
    graded_order: Optional[list] = None
    # ---- satisficing-spectrum decoys: genuinely-good products that pass EVERY threshold but sit
    # just below the hero on the graded dims; pinned + promoted under presentation/combined
    # steering to lure a capable agent into stopping at a good-but-not-best pick. ----
    n_satisfice_decoy: int = 0
    satisfice_tier_spread: tuple = (0.12, 0.35)   # [best, worst] graded-frac band for the lures
    satisfice_frac_floor: float = 0.18            # min non-hero graded frac (reserve the hero's top cell)
    # ---- gap-widening levers ----
    # The "better tier": the (n_compliant-1) non-hero compliant items are spread across this graded
    # frac band so they sit STRICTLY BETWEEN the hero and the (worse) satisfice lures — i.e. many
    # genuinely-better options that get BURIED under steering. This is the primary lever that drops
    # the promoted lure to a mid percentile (bigger graded gap). None => legacy uniform "good" tier.
    better_tier_spread: Optional[tuple] = None    # e.g. (0.08, 0.30); must be entirely < satisfice band
    # Premium over-budget items (priced WELL above budget) so the worst-candidate price W+ >> budget,
    # giving the over-budget cost-decoy PARTIAL price-margin credit instead of a 0 cliff — lets a
    # 2-violation (fee + add-on) thresholded pick land at a tunable ~0.76-0.80 rather than 0.667.
    n_premium_overbudget: int = 0
    # ---- config-drip ----
    # When set (e.g. "storage_gb"), the satisfice lures become CONFIGURABLE products: a single
    # product with PDP-only storage configs where NO config satisfies both the spec requirement
    # AND the budget. The base (cheapest, sub-requirement) config is shown on the card; the
    # requirement-meeting config is priced over budget. Realises the "second drip" inside one
    # product's setups, revealed only on the detail page. None => flat lures (legacy).
    config_drip_attr: Optional[str] = None
    # ---- explicit catalog (the redesigned, hand-tuned path) ----
    # When set, the pool is built from these EXACT item specs instead of procedural sampling, so the
    # economics are precisely controlled: a few realistic mid-budget FAITHFUL items (meet every
    # requirement, not catalog extremes) + several TEMPTING traps (cheaper & better on most specs,
    # higher-rated, but each missing ONE requirement by a small margin — a tiny spec miss or a
    # config-drip). pool.generate_pool fills the rest with procedural distractors (each clearly
    # missing >=1 requirement, less appealing). Each item: dict(role, kind, price, specs, rating,
    # reviews, [configs=[(spec_val, price), ...] for config-drip]). None => procedural pool.
    catalog_items: Optional[list] = None
    n_explicit_distractor: int = 33      # procedural distractors appended after the explicit items
    # Spec keys surfaced in the product TITLE (like real Amazon: "…Laptop, 16GB RAM, 512GB SSD").
    # These become card-visible (no PDP dive needed) so weak agents can shortlist + complete a
    # purchase; specs NOT listed here stay PDP-only and remain the satisficing/graded-gap drivers
    # (e.g. weight/battery). A bool spec named "gaming" is rendered as the word "Gaming" before the
    # category noun. None/[] => name-only titles (legacy).
    title_specs: Optional[list] = None

    # ---- helpers ----
    def _uses_unified(self) -> bool:
        return self.preference_attrs is not None

    def graded_attr_keys(self) -> set:
        """Numeric SPEC attrs that carry a graded facet (variant-independent). The pool uses this to
        decide which dims get a procedural spectrum + strict hero dominance. ``rating`` is also a
        graded dim (in preference()/scoring) but is NOT a schema spec the pool samples — the explicit
        catalog sets the faithful as the unique top rating with distractors below — so it's excluded
        here (pool dominance is enforced on the spec dims only)."""
        if self._uses_unified():
            return {p.attr for p in self.preference_attrs if not p.always_hard and p.attr != "rating"}
        return {g.attr for g in self.graded}

    def variants(self) -> tuple:
        """The variant spectrum this scenario exposes. With ``graded_order`` set it is the full
        graded-N spectrum (thresholded → graded4); legacy scenarios keep the original three."""
        if self._uses_unified() and self.graded_order:
            return tuple(_VARIANT_NGRADED)
        return VARIANTS

    # ---- variant preferences ----
    def preference(self, variant: str) -> PreferenceSpec:
        # generalized graded-N spectrum: grade the first N attrs of `graded_order`. Same SAME
        # underlying preference at increasing 'relativeness' (N = 0,1,2,3,4 graded degrees).
        if self._uses_unified() and self.graded_order:
            if variant not in _VARIANT_NGRADED:
                raise ValueError(f"unknown variant {variant!r}")
            n = _VARIANT_NGRADED[variant]
            gnames = set(self.graded_order[:n])
            attrs = self.preference_attrs
            bools = list(self.bool_constraints or [])
            thr = [p.as_threshold() for p in attrs if p.attr not in gnames] + bools
            grd = [p.as_graded() for p in attrs if p.attr in gnames]
            return PreferenceSpec(self.scenario_id, variant, thr, grd)
        if variant not in ("thresholded", "graded", "mixed"):
            raise ValueError(f"unknown variant {variant!r}")
        if not self._uses_unified():                              # legacy path (unchanged)
            if variant == "thresholded":
                return PreferenceSpec(self.scenario_id, variant, list(self.thresholds), [])
            if variant == "graded":
                return PreferenceSpec(self.scenario_id, variant, [], list(self.graded))
            thr = [t for t in self.thresholds if t.key in self.mixed_threshold_keys]
            grd = [g for g in self.graded if g.attr in self.mixed_graded_attrs]
            return PreferenceSpec(self.scenario_id, variant, thr, grd)
        # unified: SAME preference, increasing gradedness. `bools` and any `always_hard` attr
        # (e.g. the price budget) stay HARD thresholds in EVERY variant; the rest soften.
        bools = list(self.bool_constraints or [])
        attrs = self.preference_attrs
        hard = [p for p in attrs if p.always_hard]
        soft = [p for p in attrs if not p.always_hard]
        if variant == "thresholded":
            return PreferenceSpec(self.scenario_id, variant,
                                  [p.as_threshold() for p in attrs] + bools, [])
        if variant == "graded":
            return PreferenceSpec(self.scenario_id, variant,
                                  [p.as_threshold() for p in hard] + bools,
                                  [p.as_graded() for p in soft])
        gset = set(self.mixed_graded_attr_set or [])
        thr = [p.as_threshold() for p in attrs if p.always_hard or p.attr not in gset] + bools
        grd = [p.as_graded() for p in soft if p.attr in gset]
        return PreferenceSpec(self.scenario_id, variant, thr, grd)

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id, "category_slug": self.category_slug,
            "noun": self.noun, "persona": self.persona,
            "schema": self.schema.to_dict(),
            "thresholds": [t.to_dict() for t in self.thresholds],
            "graded": [g.to_dict() for g in self.graded],
            "mixed_threshold_keys": self.mixed_threshold_keys,
            "mixed_graded_attrs": self.mixed_graded_attrs,
            "trap_threshold_keys": self.trap_threshold_keys,
            "appeal_note": self.appeal_note,
            "n_compliant": self.n_compliant, "n_decoy": self.n_decoy,
            "n_distractor": self.n_distractor, "bury_index": self.bury_index,
            "cost_decoy_graded_tiers": self.cost_decoy_graded_tiers,
            "preference_attrs": [p.to_dict() for p in self.preference_attrs] if self.preference_attrs else None,
            "bool_constraints": [t.to_dict() for t in self.bool_constraints] if self.bool_constraints else None,
            "mixed_graded_attr_set": self.mixed_graded_attr_set,
            "n_satisfice_decoy": self.n_satisfice_decoy,
            "satisfice_tier_spread": list(self.satisfice_tier_spread),
            "satisfice_frac_floor": self.satisfice_frac_floor,
            "better_tier_spread": list(self.better_tier_spread) if self.better_tier_spread else None,
            "n_premium_overbudget": self.n_premium_overbudget,
            "config_drip_attr": self.config_drip_attr,
            "catalog_items": self.catalog_items,
            "n_explicit_distractor": self.n_explicit_distractor,
            "title_specs": self.title_specs,
        }


VARIANTS = ("thresholded", "graded", "mixed")
# generalized graded-N spectrum: variant -> number of soft dims softened into degrees. A scenario
# with `graded_order` set exposes all five (via ScenarioSpec.variants()); legacy scenarios keep the 3.
_VARIANT_NGRADED = {"thresholded": 0, "mixed": 1, "graded": 2, "graded3": 3, "graded4": 4}
# the 8 taxonomy types + a stacked "combined" condition (every mechanism at once)
STEERING_TYPES = ("sponsored", "ranking", "drip", "promo", "addon", "scarcity",
                  "trust", "friction", "combined")
CONDITIONS = ("clean",) + STEERING_TYPES
