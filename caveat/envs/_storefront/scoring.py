"""Binary optimal-selection scoring shared by the storefront environments."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...scoring.optimal_selection import is_optimal_selection, optimal_indices


def optimal_selection(attrs: Mapping[str, Any], preferences: Mapping[str, Any],
                      graded: Mapping[str, Any],
                      candidates: Sequence[Mapping[str, Any]]) -> float:
    """Return ``1`` for an optimal selection and ``0`` otherwise."""
    return is_optimal_selection(attrs, preferences, graded or {}, candidates)


def has_optimal_candidate(candidates: Sequence[Mapping[str, Any]],
                          preferences: Mapping[str, Any],
                          graded: Mapping[str, Any]) -> bool:
    """Whether a task has at least one evaluable optimal catalog item."""
    return bool(optimal_indices(candidates, preferences, graded or {}))
