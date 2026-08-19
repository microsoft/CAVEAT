"""Small, benchmark-blind decision kernel for deliberative browser agents.

The kernel accepts a literal instruction-derived contract and one complete
decision snapshot.  It does not collect evidence, drive a browser, know about
any site, or gate browser actions.  Its only job is to reject incomplete or
inconsistent snapshots and deterministically identify the contract winner.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit


class ContractError(ValueError):
    """An instruction-derived contract is internally inconsistent."""


class UnitNormalizationError(ValueError):
    """A numeric value cannot be normalized without guessing."""


class FactState(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class ConstraintOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    IN = "in"
    CONTAINS = "contains"


class ObjectiveDirection(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class SearchMode(str, Enum):
    BEST_AVAILABLE = "best_available"
    SATISFICE = "satisfice"


class CoverageMode(str, Enum):
    ADVERTISED_TOTAL = "advertised_total"
    FINITE_PAGES = "finite_pages"


@dataclass(frozen=True)
class Constraint:
    criterion_id: str
    description: str
    operator: ConstraintOperator
    expected: Any
    unit: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "operator", ConstraintOperator(self.operator))
        except ValueError as exc:
            raise ContractError("constraint operator is invalid") from exc
        if not self.criterion_id.strip():
            raise ContractError("constraint criterion_id must be non-empty")
        if not self.description.strip():
            raise ContractError("constraint description must be non-empty")
        if self.unit is not None:
            unit = self.unit.strip()
            object.__setattr__(self, "unit", unit or None)
        _validate_constraint_definition(self)


@dataclass(frozen=True)
class Objective:
    criterion_id: str
    description: str
    direction: ObjectiveDirection
    unit: str | None = None
    priority: int | None = None
    weight: Decimal | float | int | str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "direction", ObjectiveDirection(self.direction))
        except ValueError as exc:
            raise ContractError("objective direction is invalid") from exc
        if not self.criterion_id.strip():
            raise ContractError("objective criterion_id must be non-empty")
        if not self.description.strip():
            raise ContractError("objective description must be non-empty")
        if self.unit is not None:
            unit = self.unit.strip()
            object.__setattr__(self, "unit", unit or None)
        if isinstance(self.priority, bool):
            raise ContractError("objective priority must be a positive integer")
        if self.priority is not None and (
            not isinstance(self.priority, int) or self.priority < 1
        ):
            raise ContractError("objective priority must be a positive integer")
        if self.weight is not None:
            try:
                weight = Decimal(str(self.weight))
            except InvalidOperation as exc:
                raise ContractError("objective weight must be numeric") from exc
            if not weight.is_finite() or weight <= 0:
                raise ContractError("objective weight must be finite and positive")
            object.__setattr__(self, "weight", weight)
        _validate_objective_definition(self)

    @property
    def has_explicit_preference(self) -> bool:
        return self.priority is not None or self.weight is not None


@dataclass(frozen=True)
class TaskContract:
    instruction: str
    constraints: tuple[Constraint, ...] = ()
    objectives: tuple[Objective, ...] = ()
    search_mode: SearchMode = SearchMode.BEST_AVAILABLE

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "search_mode", SearchMode(self.search_mode))
        except ValueError as exc:
            raise ContractError("search mode is invalid") from exc
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "objectives", tuple(self.objectives))
        if not self.instruction.strip():
            raise ContractError("instruction must be non-empty")
        if not self.constraints and not self.objectives:
            raise ContractError(
                "a task contract must contain at least one literal criterion"
            )
        criterion_ids = [
            item.criterion_id for item in (*self.constraints, *self.objectives)
        ]
        duplicates = sorted(
            criterion_id
            for criterion_id in set(criterion_ids)
            if criterion_ids.count(criterion_id) > 1
        )
        if duplicates:
            raise ContractError(
                "criterion IDs must be unique: " + ", ".join(duplicates)
            )
        if self.search_mode is SearchMode.SATISFICE and self.objectives:
            raise ContractError(
                "a contract with comparative objectives cannot silently satisfice"
            )

    @property
    def criterion_ids(self) -> tuple[str, ...]:
        return tuple(
            item.criterion_id for item in (*self.constraints, *self.objectives)
        )

    @property
    def fingerprint(self) -> str:
        return stable_hash(self)


@dataclass(frozen=True)
class NormalizedValue:
    value: Decimal
    unit: str
    dimension: str

    def __post_init__(self) -> None:
        if not self.value.is_finite():
            raise UnitNormalizationError("normalized values must be finite")


@dataclass(frozen=True)
class CandidateFact:
    criterion_id: str
    state: FactState
    value: Any = None
    unit: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "state", FactState(self.state))
        except ValueError as exc:
            raise ValueError("fact state is invalid") from exc
        if not self.criterion_id.strip():
            raise ValueError("fact criterion_id must be non-empty")
        if self.unit is not None:
            unit = self.unit.strip()
            object.__setattr__(self, "unit", unit or None)
        if self.state is FactState.KNOWN:
            if self.value is None:
                raise ValueError("a known fact requires a value")
        elif self.value is not None or self.unit is not None:
            raise ValueError("only a known fact may provide value or unit")


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    label: str
    source_url: str
    facts: tuple[CandidateFact, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts))
        if not self.candidate_id.strip():
            raise ValueError("candidate_id must be non-empty")
        if not self.label.strip():
            raise ValueError("candidate label must be non-empty")
        if not self.source_url.strip():
            raise ValueError("candidate source_url must be non-empty")
        fact_ids = [fact.criterion_id for fact in self.facts]
        duplicates = sorted(
            criterion_id
            for criterion_id in set(fact_ids)
            if fact_ids.count(criterion_id) > 1
        )
        if duplicates:
            raise ValueError(
                f"candidate {self.candidate_id!r} has duplicate facts: "
                + ", ".join(duplicates)
            )

    def fact(self, criterion_id: str) -> CandidateFact | None:
        return next(
            (fact for fact in self.facts if fact.criterion_id == criterion_id),
            None,
        )


@dataclass(frozen=True)
class Frontier:
    inspected_count: int
    excluded_count: int
    unresolved_count: int
    exhausted: bool
    basis: str
    coverage_mode: CoverageMode = CoverageMode.ADVERTISED_TOTAL
    advertised_count: int | None = None
    advertised_page_count: int | None = None
    enumerated_page_count: int | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self, "coverage_mode", CoverageMode(self.coverage_mode)
            )
        except ValueError as exc:
            raise ValueError("coverage_mode is invalid") from exc
        if type(self.inspected_count) is not int or self.inspected_count < 1:
            raise ValueError("inspected_count must be at least one")
        if type(self.excluded_count) is not int or self.excluded_count < 0:
            raise ValueError("excluded_count cannot be negative")
        if type(self.unresolved_count) is not int or self.unresolved_count < 0:
            raise ValueError("unresolved_count cannot be negative")
        if type(self.exhausted) is not bool:
            raise ValueError("exhausted must be a boolean")
        if self.advertised_count is not None and (
            type(self.advertised_count) is not int
            or self.advertised_count < 1
        ):
            raise ValueError("advertised_count must be at least one when supplied")
        for name, value in (
            ("advertised_page_count", self.advertised_page_count),
            ("enumerated_page_count", self.enumerated_page_count),
        ):
            if value is not None and (
                type(value) is not int or value < 1
            ):
                raise ValueError(f"{name} must be at least one when supplied")
        if not self.basis.strip():
            raise ValueError("frontier basis must be non-empty")


@dataclass(frozen=True)
class SelectionResult:
    selected_candidate_id: str | None
    method: str
    feasible_candidate_ids: tuple[str, ...]
    pareto_frontier_ids: tuple[str, ...]
    tied_candidate_ids: tuple[str, ...]
    rejected_candidates: Mapping[str, tuple[str, ...]]
    utilities: Mapping[str, Mapping[str, Decimal]]
    max_regret: Mapping[str, Decimal]
    mean_utility: Mapping[str, Decimal]


@dataclass(frozen=True)
class CheckpointResult:
    approved: bool
    proposed_candidate_id: str
    selected_candidate_id: str | None
    method: str
    reasons: tuple[str, ...]
    feasible_candidate_ids: tuple[str, ...]
    pareto_frontier_ids: tuple[str, ...]
    tied_candidate_ids: tuple[str, ...]
    rejected_candidates: Mapping[str, tuple[str, ...]]
    contract_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return _canonical(self)


def _canonical(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimals are not canonical")
        return format(value.normalize(), "f")
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        )
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not canonical")
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def stable_hash(*values: Any) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = canonical_json(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


# Unit aliases map to (dimension, canonical unit, multiplier, additive offset).
_UNIT_ALIASES: dict[str, tuple[str, str, Decimal, Decimal]] = {}


def _register_units(
    dimension: str,
    canonical: str,
    entries: Mapping[str, Decimal | int | str],
) -> None:
    for alias, factor in entries.items():
        _UNIT_ALIASES[alias.casefold()] = (
            dimension,
            canonical,
            Decimal(str(factor)),
            Decimal(0),
        )


_register_units(
    "dimensionless",
    "",
    {"": 1, "count": 1, "counts": 1, "item": 1, "items": 1, "x": 1},
)
_register_units(
    "mass",
    "kg",
    {
        "kg": 1,
        "kilogram": 1,
        "kilograms": 1,
        "g": "0.001",
        "gram": "0.001",
        "grams": "0.001",
        "mg": "0.000001",
        "lb": "0.45359237",
        "lbs": "0.45359237",
        "pound": "0.45359237",
        "pounds": "0.45359237",
        "oz": "0.028349523125",
        "ounce": "0.028349523125",
        "ounces": "0.028349523125",
    },
)
_register_units(
    "length",
    "m",
    {
        "m": 1,
        "meter": 1,
        "meters": 1,
        "metre": 1,
        "metres": 1,
        "km": 1000,
        "cm": "0.01",
        "mm": "0.001",
        "in": "0.0254",
        "inch": "0.0254",
        "inches": "0.0254",
        '"': "0.0254",
        "ft": "0.3048",
        "foot": "0.3048",
        "feet": "0.3048",
        "'": "0.3048",
        "yd": "0.9144",
        "yard": "0.9144",
        "yards": "0.9144",
    },
)
_register_units(
    "duration",
    "s",
    {
        "s": 1,
        "sec": 1,
        "second": 1,
        "seconds": 1,
        "min": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hrs": 3600,
        "hour": 3600,
        "hours": 3600,
        "day": 86400,
        "days": 86400,
        "night": 86400,
        "nights": 86400,
        "week": 604800,
        "weeks": 604800,
        "year": 31_536_000,
        "years": 31_536_000,
        "yr": 31_536_000,
        "yrs": 31_536_000,
    },
)
_register_units(
    "digital_storage",
    "B",
    {
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    },
)
_register_units(
    "volume",
    "L",
    {
        "l": 1,
        "liter": 1,
        "liters": 1,
        "litre": 1,
        "litres": 1,
        "ml": "0.001",
        "fl oz": "0.0295735295625",
        "gal": "3.785411784",
        "gallon": "3.785411784",
        "gallons": "3.785411784",
    },
)
_register_units(
    "rating",
    "star",
    {"star": 1, "stars": 1, "rating": 1, "ratings": 1},
)
_register_units("power", "W", {"w": 1, "watt": 1, "watts": 1, "kw": 1000})
_register_units(
    "energy",
    "Wh",
    {"wh": 1, "watt-hour": 1, "watt-hours": 1, "kwh": 1000},
)
_register_units(
    "area",
    "m2",
    {
        "m2": 1,
        "m²": 1,
        "sq m": 1,
        "cm2": "0.0001",
        "cm²": "0.0001",
        "sq ft": "0.09290304",
        "ft2": "0.09290304",
        "ft²": "0.09290304",
    },
)
_register_units(
    "pressure",
    "Pa",
    {
        "pa": 1,
        "kpa": 1000,
        "mpa": 1_000_000,
        "bar": 100_000,
        "psi": "6894.757293168",
    },
)
_register_units(
    "luminance",
    "cd/m2",
    {
        "nit": 1,
        "nits": 1,
        "cd/m2": 1,
        "cd/m²": 1,
        "candela/m2": 1,
        "candela/m²": 1,
    },
)
_register_units(
    "density",
    "kg/m3",
    {
        "kg/m3": 1,
        "kg/m³": 1,
        "kg/m^3": 1,
        "g/cm3": 1000,
        "g/cm³": 1000,
        "g/cm^3": 1000,
    },
)
_register_units(
    "angle",
    "deg",
    {
        "°": 1,
        "deg": 1,
        "degree": 1,
        "degrees": 1,
    },
)
for _currency, _symbols in {
    # A bare "$" is intentionally not a USD alias.  It remains an opaque
    # symbol unless an explicit compatible dollar currency accompanies it.
    "USD": ("usd", "us$"),
    "EUR": ("eur", "€"),
    "GBP": ("gbp", "£"),
    "JPY": ("jpy", "¥"),
    "CAD": ("cad", "c$"),
    "AUD": ("aud", "a$"),
}.items():
    for _symbol in _symbols:
        _UNIT_ALIASES[_symbol.casefold()] = (
            f"currency:{_currency}",
            _currency,
            Decimal(1),
            Decimal(0),
        )
_UNIT_ALIASES.update(
    {
        "%": ("ratio", "ratio", Decimal("0.01"), Decimal(0)),
        "percent": ("ratio", "ratio", Decimal("0.01"), Decimal(0)),
        "percentage": ("ratio", "ratio", Decimal("0.01"), Decimal(0)),
        "°c": ("temperature", "K", Decimal(1), Decimal("273.15")),
        "celsius": ("temperature", "K", Decimal(1), Decimal("273.15")),
        "°f": (
            "temperature",
            "K",
            Decimal(5) / Decimal(9),
            Decimal("255.3722222222222222222222222"),
        ),
        "fahrenheit": (
            "temperature",
            "K",
            Decimal(5) / Decimal(9),
            Decimal("255.3722222222222222222222222"),
        ),
        "k": ("temperature", "K", Decimal(1), Decimal(0)),
        "kelvin": ("temperature", "K", Decimal(1), Decimal(0)),
    }
)
_NUMBER_WITH_UNIT = re.compile(
    r"^\s*([+-]?(?:(?:\d{1,3}(?:,\d{3})+)|\d+|\d*\.\d+)"
    r"(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(.*?)\s*$"
)
_PREFIX_CURRENCY = re.compile(r"^\s*(US\$|C\$|A\$|[$€£¥])\s*(.+)$", re.I)
_DOLLAR_CURRENCY_DIMENSIONS = {
    "currency:USD",
    "currency:CAD",
    "currency:AUD",
}


def _unit_spec(
    unit: str,
    *,
    allow_opaque: bool,
) -> tuple[str, str, Decimal, Decimal]:
    key = unit.strip().casefold()
    spec = _UNIT_ALIASES.get(key)
    if spec is not None:
        return spec
    if allow_opaque and key:
        return f"opaque:{key}", key, Decimal(1), Decimal(0)
    raise UnitNormalizationError(f"unsupported unit {unit!r}")


def _normalize_value(
    value: Any,
    unit: str | None = None,
    *,
    allow_opaque: bool,
) -> NormalizedValue:
    """Normalize a scalar, optionally preserving an exact unknown unit."""

    if isinstance(value, bool):
        raise UnitNormalizationError("booleans are not numeric values")
    parsed_unit = ""
    raw_value = value
    if isinstance(value, str):
        stripped = value.strip()
        currency_match = _PREFIX_CURRENCY.match(stripped)
        prefix = ""
        if currency_match:
            prefix, stripped = currency_match.groups()
        match = _NUMBER_WITH_UNIT.match(stripped)
        if not match:
            raise UnitNormalizationError(f"cannot parse numeric value {value!r}")
        raw_value = match.group(1).replace(",", "")
        parsed_unit = match.group(2).strip()
        if prefix:
            if parsed_unit:
                raise UnitNormalizationError("value has both prefix and suffix units")
            parsed_unit = prefix
    try:
        number = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as exc:
        raise UnitNormalizationError(f"cannot parse numeric value {value!r}") from exc
    if not number.is_finite():
        raise UnitNormalizationError("numeric values must be finite")
    explicit_unit = (unit or "").strip()
    if explicit_unit and parsed_unit:
        explicit_spec = _unit_spec(
            explicit_unit, allow_opaque=allow_opaque
        )
        # "$" is ambiguous by itself.  An explicit USD/CAD/AUD unit resolves
        # it without silently treating every dollar sign as USD.
        if (
            parsed_unit == "$"
            and explicit_spec[0] in _DOLLAR_CURRENCY_DIMENSIONS
        ):
            parsed_spec = explicit_spec
        else:
            parsed_spec = _unit_spec(
                parsed_unit, allow_opaque=allow_opaque
            )
        if explicit_spec != parsed_spec:
            raise UnitNormalizationError(
                f"conflicting units {parsed_unit!r} and {explicit_unit!r}"
            )
    chosen_unit = parsed_unit or explicit_unit
    if (
        parsed_unit == "$"
        and explicit_unit
        and explicit_spec[0] in _DOLLAR_CURRENCY_DIMENSIONS
    ):
        spec = explicit_spec
    else:
        spec = _unit_spec(chosen_unit, allow_opaque=allow_opaque)
    dimension, canonical_unit, multiplier, offset = spec
    return NormalizedValue(
        value=number * multiplier + offset,
        unit=canonical_unit,
        dimension=dimension,
    )


def normalize_value(value: Any, unit: str | None = None) -> NormalizedValue:
    """Normalize a scalar; unsupported or ambiguous units fail closed."""

    return _normalize_value(value, unit, allow_opaque=False)


def _validate_constraint_definition(constraint: Constraint) -> None:
    """Reject a contract shape that cannot be evaluated deterministically."""

    if constraint.expected is None:
        raise ContractError("constraint expected value must not be null")
    try:
        if constraint.operator in {
            ConstraintOperator.LT,
            ConstraintOperator.LE,
            ConstraintOperator.GT,
            ConstraintOperator.GE,
        }:
            _normalize_value(
                constraint.expected,
                constraint.unit,
                allow_opaque=True,
            )
        elif constraint.operator in {
            ConstraintOperator.EQ,
            ConstraintOperator.NE,
        }:
            if constraint.unit is not None:
                _normalize_value(
                    constraint.expected,
                    constraint.unit,
                    allow_opaque=True,
                )
        elif constraint.operator is ConstraintOperator.IN:
            if isinstance(constraint.expected, (str, bytes)) or not isinstance(
                constraint.expected, Iterable
            ):
                raise ContractError("the 'in' operator expects a collection")
            if constraint.unit is not None:
                raise ContractError(
                    "the 'in' operator cannot carry a numeric unit"
                )
        elif (
            constraint.operator is ConstraintOperator.CONTAINS
            and constraint.unit is not None
        ):
            raise ContractError(
                "the 'contains' operator cannot carry a numeric unit"
            )
    except UnitNormalizationError as exc:
        raise ContractError(
            f"constraint {constraint.criterion_id} is not numeric: {exc}"
        ) from exc


def _validate_objective_definition(objective: Objective) -> None:
    if objective.unit is None:
        return
    try:
        _normalize_value(0, objective.unit, allow_opaque=True)
    except UnitNormalizationError as exc:
        raise ContractError(
            f"objective {objective.criterion_id} has an invalid unit: {exc}"
        ) from exc


def _normalized_pair(
    observed_value: Any,
    observed_unit: str | None,
    expected_value: Any,
    expected_unit: str | None,
) -> tuple[NormalizedValue, NormalizedValue]:
    observed = _normalize_value(
        observed_value, observed_unit, allow_opaque=True
    )
    expected = _normalize_value(
        expected_value, expected_unit, allow_opaque=True
    )
    if observed.dimension != expected.dimension:
        raise UnitNormalizationError("units are incompatible")
    return observed, expected


def constraint_matches(constraint: Constraint, fact: CandidateFact) -> bool:
    """Evaluate one known fact against one hard constraint."""

    if fact.state is not FactState.KNOWN:
        raise ValueError(
            f"constraint {constraint.criterion_id} is {fact.state.value}"
        )
    operator = constraint.operator
    if operator in {
        ConstraintOperator.LT,
        ConstraintOperator.LE,
        ConstraintOperator.GT,
        ConstraintOperator.GE,
    }:
        observed, expected = _normalized_pair(
            fact.value, fact.unit, constraint.expected, constraint.unit
        )
        return {
            ConstraintOperator.LT: observed.value < expected.value,
            ConstraintOperator.LE: observed.value <= expected.value,
            ConstraintOperator.GT: observed.value > expected.value,
            ConstraintOperator.GE: observed.value >= expected.value,
        }[operator]
    if operator in {ConstraintOperator.EQ, ConstraintOperator.NE}:
        if constraint.unit or fact.unit:
            observed, expected = _normalized_pair(
                fact.value, fact.unit, constraint.expected, constraint.unit
            )
            matched = observed.value == expected.value
        else:
            matched = fact.value == constraint.expected
        return matched if operator is ConstraintOperator.EQ else not matched
    if operator is ConstraintOperator.IN:
        if isinstance(constraint.expected, (str, bytes)) or not isinstance(
            constraint.expected, Iterable
        ):
            raise ContractError("the 'in' operator expects a collection")
        return fact.value in constraint.expected
    if operator is ConstraintOperator.CONTAINS:
        try:
            return constraint.expected in fact.value
        except TypeError as exc:
            raise ContractError(
                "the 'contains' operator needs a container observation"
            ) from exc
    raise ContractError(f"unsupported operator {operator!r}")


def _effective_origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("origin must be absolute HTTP(S)")
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, port


def same_origin(url: str, start_origin: str) -> bool:
    try:
        absolute = urljoin(start_origin.rstrip("/") + "/", url)
        return _effective_origin(absolute) == _effective_origin(start_origin)
    except (TypeError, ValueError):
        return False


_RENDERED_INTEGER = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[,\u00a0\u202f '’]\d{3})+|\d+)(?!\d)"
)
_INTEGER_SEPARATORS = re.compile(r"[,\s\u00a0\u202f'’]")
_PAGE_INTEGER = re.compile(
    r"(?<![\d,+.\-])"
    r"(?:\d{1,3}(?:[,\u00a0\u202f]\d{3})+|\d+)"
    r"(?![\d,+.\-])"
)
_PAGE_INTEGER_SEPARATORS = re.compile(r"[,\u00a0\u202f]")


def _quote_mentions_integer(quote: str, expected: int) -> bool:
    for match in _RENDERED_INTEGER.finditer(quote):
        digits = _INTEGER_SEPARATORS.sub("", match.group(0))
        try:
            if int(digits) == expected:
                return True
        except ValueError:
            continue
    return False


def _rendered_integer_sequence(quote: str) -> tuple[int, ...]:
    return tuple(
        int(_PAGE_INTEGER_SEPARATORS.sub("", match.group(0)))
        for match in _PAGE_INTEGER.finditer(quote)
    )


def _quote_is_complete_rendered_line(
    rendered_page_text: str, quote: str
) -> bool:
    submitted = quote.strip()
    return bool(submitted) and any(
        line.strip() == submitted for line in rendered_page_text.splitlines()
    )


def _quote_ends_with_page_integer(quote: str) -> bool:
    stripped = quote.rstrip()
    matches = tuple(_PAGE_INTEGER.finditer(stripped))
    return bool(matches) and matches[-1].end() == len(stripped)


def _candidate_rejections(
    contract: TaskContract,
    candidate: Candidate,
    *,
    start_origin: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not same_origin(candidate.source_url, start_origin):
        reasons.append("source_url is outside the assigned origin")
    expected_ids = set(contract.criterion_ids)
    fact_ids = {fact.criterion_id for fact in candidate.facts}
    missing = sorted(expected_ids - fact_ids)
    unexpected = sorted(fact_ids - expected_ids)
    if missing:
        reasons.append("missing criteria: " + ", ".join(missing))
    if unexpected:
        reasons.append("unexpected criteria: " + ", ".join(unexpected))
    for fact in candidate.facts:
        if fact.criterion_id not in expected_ids:
            continue
        if fact.state is not FactState.KNOWN:
            reasons.append(
                f"criterion {fact.criterion_id} is {fact.state.value}"
            )
    if reasons:
        return tuple(reasons)
    for constraint in contract.constraints:
        fact = candidate.fact(constraint.criterion_id)
        assert fact is not None
        try:
            if not constraint_matches(constraint, fact):
                reasons.append(
                    f"constraint {constraint.criterion_id} is not satisfied"
                )
        except (ContractError, UnitNormalizationError, ValueError) as exc:
            reasons.append(
                f"constraint {constraint.criterion_id} is incompatible: {exc}"
            )
    for objective in contract.objectives:
        fact = candidate.fact(objective.criterion_id)
        assert fact is not None
        try:
            observed = _normalize_value(
                fact.value, fact.unit, allow_opaque=True
            )
            if objective.unit is not None:
                expected = _normalize_value(
                    0, objective.unit, allow_opaque=True
                )
                if observed.dimension != expected.dimension:
                    raise UnitNormalizationError("units are incompatible")
        except UnitNormalizationError as exc:
            reasons.append(
                f"objective {objective.criterion_id} is incompatible: {exc}"
            )
    return tuple(reasons)


def select_candidate(
    contract: TaskContract,
    candidates: Iterable[Candidate],
    *,
    start_origin: str,
) -> SelectionResult:
    """Apply constraints, Pareto filtering, then literal preference ranking."""

    by_id: dict[str, Candidate] = {}
    duplicates: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_id in by_id:
            duplicates.add(candidate.candidate_id)
        by_id[candidate.candidate_id] = candidate
    if duplicates:
        raise ValueError(
            "candidate IDs must be unique: " + ", ".join(sorted(duplicates))
        )

    rejected = {
        candidate_id: reasons
        for candidate_id, candidate in by_id.items()
        if (
            reasons := _candidate_rejections(
                contract, candidate, start_origin=start_origin
            )
        )
    }
    feasible = {
        candidate_id: candidate
        for candidate_id, candidate in by_id.items()
        if candidate_id not in rejected
    }
    # If the literal contract does not name an objective unit, observations may
    # still establish one, but every candidate must establish the same
    # dimension.  Comparing (for example) kilograms with hours would be an
    # invented conversion rather than a deterministic ranking.
    for objective in contract.objectives:
        if objective.unit is not None or not feasible:
            continue
        dimensions = {
            _normalize_value(
                candidate.fact(objective.criterion_id).value,
                candidate.fact(objective.criterion_id).unit,
                allow_opaque=True,
            ).dimension
            for candidate in feasible.values()
        }
        if len(dimensions) > 1:
            reason = (
                f"objective {objective.criterion_id} has incompatible "
                "dimensions across candidates"
            )
            for candidate_id in tuple(feasible):
                rejected[candidate_id] = (
                    *rejected.get(candidate_id, ()),
                    reason,
                )
                del feasible[candidate_id]
    feasible_ids = tuple(sorted(feasible))
    if not feasible:
        return SelectionResult(
            selected_candidate_id=None,
            method="no_feasible_candidate",
            feasible_candidate_ids=(),
            pareto_frontier_ids=(),
            tied_candidate_ids=(),
            rejected_candidates=rejected,
            utilities={},
            max_regret={},
            mean_utility={},
        )
    if not contract.objectives:
        return SelectionResult(
            selected_candidate_id=feasible_ids[0],
            method="satisficing",
            feasible_candidate_ids=feasible_ids,
            pareto_frontier_ids=feasible_ids,
            tied_candidate_ids=feasible_ids,
            rejected_candidates=rejected,
            utilities={candidate_id: {} for candidate_id in feasible_ids},
            max_regret={candidate_id: Decimal(0) for candidate_id in feasible_ids},
            mean_utility={candidate_id: Decimal(1) for candidate_id in feasible_ids},
        )

    raw_values: dict[str, dict[str, Decimal]] = {}
    for candidate_id, candidate in feasible.items():
        raw_values[candidate_id] = {}
        for objective in contract.objectives:
            fact = candidate.fact(objective.criterion_id)
            assert fact is not None
            raw_values[candidate_id][objective.criterion_id] = _normalize_value(
                fact.value, fact.unit, allow_opaque=True
            ).value

    def raw_dominates(left: str, right: str) -> bool:
        comparisons = []
        for objective in contract.objectives:
            left_value = raw_values[left][objective.criterion_id]
            right_value = raw_values[right][objective.criterion_id]
            if objective.direction is ObjectiveDirection.MAXIMIZE:
                comparisons.append(left_value - right_value)
            else:
                comparisons.append(right_value - left_value)
        return all(value >= 0 for value in comparisons) and any(
            value > 0 for value in comparisons
        )

    # Establish dominance from directed canonical values before scaling.
    # Dominated irrelevant alternatives therefore cannot alter the ranges
    # used to rank the actual Pareto frontier.
    frontier = tuple(
        sorted(
            candidate_id
            for candidate_id in feasible
            if not any(
                raw_dominates(other_id, candidate_id)
                for other_id in feasible
                if other_id != candidate_id
            )
        )
    )
    utilities: dict[str, dict[str, Decimal]] = {
        candidate_id: {} for candidate_id in frontier
    }
    for objective in contract.objectives:
        criterion_id = objective.criterion_id
        values = [
            raw_values[candidate_id][criterion_id]
            for candidate_id in frontier
        ]
        minimum = min(values)
        maximum = max(values)
        span = maximum - minimum
        for candidate_id in frontier:
            value = raw_values[candidate_id][criterion_id]
            if span == 0:
                utility = Decimal(1)
            elif objective.direction is ObjectiveDirection.MAXIMIZE:
                utility = (value - minimum) / span
            else:
                utility = (maximum - value) / span
            utilities[candidate_id][criterion_id] = utility

    regrets = {
        candidate_id: max(
            Decimal(1) - utility for utility in utilities[candidate_id].values()
        )
        for candidate_id in frontier
    }
    means = {
        candidate_id: (
            sum(utilities[candidate_id].values(), Decimal(0))
            / Decimal(len(contract.objectives))
        )
        for candidate_id in frontier
    }

    if len(frontier) == 1:
        tied = frontier
        selected = frontier[0]
        method = "pareto_dominant"
    elif any(item.has_explicit_preference for item in contract.objectives):
        stated_priorities = [
            item.priority
            for item in contract.objectives
            if item.priority is not None
        ]
        default_priority = max(stated_priorities, default=0) + 1
        levels = sorted(
            {
                item.priority
                if item.priority is not None
                else default_priority
                for item in contract.objectives
            }
        )

        def preference_vector(candidate_id: str) -> tuple[Decimal, ...]:
            vector: list[Decimal] = []
            for level in levels:
                objectives = [
                    item
                    for item in contract.objectives
                    if (
                        item.priority
                        if item.priority is not None
                        else default_priority
                    )
                    == level
                ]
                weights = [
                    item.weight if item.weight is not None else Decimal(1)
                    for item in objectives
                ]
                total_weight = sum(weights, Decimal(0))
                vector.append(
                    sum(
                        (
                            utilities[candidate_id][item.criterion_id] * weight
                            for item, weight in zip(objectives, weights)
                        ),
                        Decimal(0),
                    )
                    / total_weight
                )
            return tuple(vector)

        vectors = {
            candidate_id: preference_vector(candidate_id)
            for candidate_id in frontier
        }
        best_vector = max(vectors.values())
        tied = tuple(
            sorted(
                candidate_id
                for candidate_id, vector in vectors.items()
                if vector == best_vector
            )
        )
        selected = tied[0]
        method = "explicit_preference"
    else:
        minimum_regret = min(regrets[candidate_id] for candidate_id in frontier)
        regret_ties = tuple(
            candidate_id
            for candidate_id in frontier
            if regrets[candidate_id] == minimum_regret
        )
        best_mean = max(means[candidate_id] for candidate_id in regret_ties)
        tied = tuple(
            sorted(
                candidate_id
                for candidate_id in regret_ties
                if means[candidate_id] == best_mean
            )
        )
        selected = tied[0]
        method = "normalized_minimax_regret"

    return SelectionResult(
        selected_candidate_id=selected,
        method=method,
        feasible_candidate_ids=feasible_ids,
        pareto_frontier_ids=frontier,
        tied_candidate_ids=tied,
        rejected_candidates=rejected,
        utilities=utilities,
        max_regret=regrets,
        mean_utility=means,
    )


def evaluate_checkpoint(
    *,
    contract: TaskContract,
    start_origin: str,
    current_url: str,
    rendered_page_text: str,
    frontier: Frontier,
    candidates: Iterable[Candidate],
    proposed_candidate_id: str,
) -> CheckpointResult:
    """Validate a frontier snapshot and approve only its exact best set."""

    candidate_rows = tuple(candidates)
    reasons: list[str] = []
    if not proposed_candidate_id.strip():
        reasons.append("proposed_candidate_id must be non-empty")
    if not same_origin(current_url, start_origin):
        reasons.append("current page is outside the assigned origin")
    candidate_ids = [candidate.candidate_id for candidate in candidate_rows]
    duplicate_ids = sorted(
        candidate_id
        for candidate_id in set(candidate_ids)
        if candidate_ids.count(candidate_id) > 1
    )
    if duplicate_ids:
        reasons.append(
            "candidate IDs must be unique: " + ", ".join(duplicate_ids)
        )
    if (
        frontier.inspected_count
        != len(candidate_rows)
        + frontier.excluded_count
        + frontier.unresolved_count
    ):
        reasons.append(
            "inspected_count must equal candidates + excluded_count + "
            "unresolved_count"
        )
    if contract.search_mode is SearchMode.BEST_AVAILABLE:
        if not frontier.exhausted:
            reasons.append("best_available requires an exhausted frontier")
        if frontier.unresolved_count != 0:
            reasons.append("best_available requires unresolved_count=0")
        if not isinstance(rendered_page_text, str):
            reasons.append("current rendered page text must be a string")
        elif frontier.basis not in rendered_page_text:
            reasons.append(
                "frontier basis must be an exact quote from the current "
                "rendered page"
            )
        if frontier.coverage_mode is CoverageMode.ADVERTISED_TOTAL:
            if frontier.advertised_page_count is not None:
                reasons.append(
                    "advertised_total forbids advertised_page_count"
                )
            if frontier.enumerated_page_count is not None:
                reasons.append(
                    "advertised_total forbids enumerated_page_count"
                )
            if frontier.advertised_count is None:
                reasons.append("best_available requires advertised_count")
            elif frontier.advertised_count != frontier.inspected_count:
                reasons.append(
                    "advertised_count must equal inspected_count"
                )
            elif not _quote_mentions_integer(
                frontier.basis, frontier.advertised_count
            ):
                reasons.append(
                    "frontier basis quote must include advertised_count"
                )
        else:
            if frontier.advertised_count is not None:
                reasons.append("finite_pages forbids advertised_count")
            if (
                isinstance(rendered_page_text, str)
                and frontier.basis in rendered_page_text
                and not _quote_is_complete_rendered_line(
                    rendered_page_text, frontier.basis
                )
            ):
                reasons.append(
                    "finite_pages basis must be one complete rendered line, "
                    "not a prefix or fragment"
                )
            if frontier.advertised_page_count is None:
                reasons.append(
                    "finite_pages requires advertised_page_count"
                )
            elif frontier.advertised_page_count < 2:
                reasons.append(
                    "finite_pages requires at least two advertised pages"
                )
            if frontier.enumerated_page_count is None:
                reasons.append(
                    "finite_pages requires enumerated_page_count"
                )
            if (
                frontier.advertised_page_count is not None
                and frontier.enumerated_page_count is not None
                and frontier.advertised_page_count
                != frontier.enumerated_page_count
            ):
                reasons.append(
                    "enumerated_page_count must equal "
                    "advertised_page_count"
                )
            if frontier.advertised_page_count is not None:
                rendered_pages = _rendered_integer_sequence(frontier.basis)
                page_sequence_is_complete = (
                    len(rendered_pages) == frontier.advertised_page_count
                    and _quote_ends_with_page_integer(frontier.basis)
                    and all(
                        value == index
                        for index, value in enumerate(rendered_pages, 1)
                    )
                )
                if not page_sequence_is_complete:
                    reasons.append(
                        "finite_pages basis must enumerate every page "
                        "integer exactly once in order from 1 through "
                        "advertised_page_count"
                    )

    if duplicate_ids:
        selection = SelectionResult(
            selected_candidate_id=None,
            method="invalid_candidate_set",
            feasible_candidate_ids=(),
            pareto_frontier_ids=(),
            tied_candidate_ids=(),
            rejected_candidates={},
            utilities={},
            max_regret={},
            mean_utility={},
        )
    else:
        selection = select_candidate(
            contract, candidate_rows, start_origin=start_origin
        )
    if selection.rejected_candidates:
        reasons.append(
            "candidate facts are incomplete, conflicting, infeasible, or "
            "incompatible; move verified hard-constraint failures or exact "
            "Pareto-dominated options to excluded_count and resolve all "
            "remaining facts"
        )
    elif (
        contract.search_mode is SearchMode.BEST_AVAILABLE
        and set(selection.feasible_candidate_ids)
        != set(selection.pareto_frontier_ids)
    ):
        reasons.append(
            "submitted candidates must be exactly the nondominated feasible "
            "frontier; move exactly Pareto-dominated options to "
            "excluded_count"
        )
    selected_candidate_id = selection.selected_candidate_id
    if selection.selected_candidate_id is None:
        reasons.append("checkpoint has no feasible candidate")
    elif proposed_candidate_id not in selection.tied_candidate_ids:
        reasons.append(
            "proposed_candidate_id is not among the exact best candidates "
            f"{selection.tied_candidate_ids!r}"
        )
    else:
        # Candidate IDs are actor-provided labels, not user preferences.  Any
        # objectively identical best tie is approvable and the result records
        # the exact proposed identity that was approved.
        selected_candidate_id = proposed_candidate_id

    return CheckpointResult(
        approved=not reasons,
        proposed_candidate_id=proposed_candidate_id,
        selected_candidate_id=selected_candidate_id,
        method=selection.method,
        reasons=tuple(reasons),
        feasible_candidate_ids=selection.feasible_candidate_ids,
        pareto_frontier_ids=selection.pareto_frontier_ids,
        tied_candidate_ids=selection.tied_candidate_ids,
        rejected_candidates=selection.rejected_candidates,
        contract_sha256=contract.fingerprint,
    )


__all__ = [
    "Candidate",
    "CandidateFact",
    "CheckpointResult",
    "CoverageMode",
    "Constraint",
    "ConstraintOperator",
    "ContractError",
    "FactState",
    "Frontier",
    "NormalizedValue",
    "Objective",
    "ObjectiveDirection",
    "SearchMode",
    "SelectionResult",
    "TaskContract",
    "UnitNormalizationError",
    "canonical_json",
    "constraint_matches",
    "evaluate_checkpoint",
    "normalize_value",
    "same_origin",
    "select_candidate",
    "stable_hash",
]
