from __future__ import annotations

from decimal import Decimal

import pytest

from agentarena.scaffolds._deliberative_core import (
    Candidate,
    CandidateFact,
    Constraint,
    ContractError,
    CoverageMode,
    FactState,
    Frontier,
    Objective,
    TaskContract,
    UnitNormalizationError,
    canonical_json,
    constraint_matches,
    evaluate_checkpoint,
    normalize_value,
    same_origin,
    select_candidate,
    stable_hash,
)


ORIGIN = "https://example.test"


def _contract(*, priorities=False) -> TaskContract:
    return TaskContract(
        "Choose the lightest qualifying option with the longest runtime.",
        constraints=(
            Constraint("cost", "cost at most 100 USD", "le", 100, "USD"),
        ),
        objectives=(
            Objective(
                "mass",
                "minimize mass",
                "minimize",
                "kg",
                priority=1 if priorities else None,
            ),
            Objective(
                "runtime",
                "maximize runtime",
                "maximize",
                "h",
                priority=2 if priorities else None,
            ),
        ),
    )


def _candidate(
    candidate_id: str,
    *,
    cost: float = 90,
    mass: float = 2,
    runtime: float = 8,
    origin: str = ORIGIN,
) -> Candidate:
    return Candidate(
        candidate_id,
        candidate_id,
        f"{origin}/{candidate_id}",
        (
            CandidateFact("cost", "known", cost, "USD"),
            CandidateFact("mass", "known", mass, "kg"),
            CandidateFact("runtime", "known", runtime, "h"),
        ),
    )


def _frontier(count: int, **updates) -> Frontier:
    values = {
        "inspected_count": count,
        "advertised_count": count,
        "excluded_count": 0,
        "unresolved_count": 0,
        "exhausted": True,
        "basis": f"Showing all {count:,} results",
    }
    values.update(updates)
    return Frontier(**values)


def test_contract_requires_unique_literal_criteria():
    with pytest.raises(ContractError, match="unique"):
        TaskContract(
            "Choose.",
            constraints=(Constraint("same", "required", "eq", True),),
            objectives=(Objective("same", "maximize", "maximize", "count"),),
        )


def test_contract_rejects_empty_and_semantically_invalid_definitions():
    with pytest.raises(ContractError, match="at least one literal criterion"):
        TaskContract("Choose the lightest item.")
    with pytest.raises(ContractError, match="must not be null"):
        Constraint("mass", "mass limit", "le", None, "kg")
    with pytest.raises(ContractError, match="not numeric"):
        Constraint("mass", "mass limit", "le", "many", "kg")
    with pytest.raises(ContractError, match="expects a collection"):
        Constraint("color", "allowed colors", "in", "red")
    with pytest.raises(ContractError, match="cannot carry"):
        Constraint("color", "allowed colors", "in", ["red"], "kg")


def test_fact_states_keep_unknown_and_conflict_distinct_from_known():
    assert CandidateFact("mass", "unknown").state is FactState.UNKNOWN
    assert CandidateFact("mass", "conflict").state is FactState.CONFLICT
    assert CandidateFact("mass", "known", 2, "kg").state is FactState.KNOWN
    with pytest.raises(ValueError, match="requires a value"):
        CandidateFact("mass", "known")
    with pytest.raises(ValueError, match="only a known"):
        CandidateFact("mass", "unknown", 2, "kg")


def test_units_normalize_without_guessing():
    normalized = normalize_value("2.20462262185 lb")
    assert normalized.unit == "kg"
    assert normalized.value == Decimal("1.0000000000005552845")
    with pytest.raises(UnitNormalizationError):
        normalize_value(2, "furlongs")


def test_required_units_and_exact_opaque_units_are_supported():
    assert normalize_value("250 nits").dimension == "luminance"
    assert normalize_value("250 cd/m²").value == Decimal(250)
    assert normalize_value("30 kg/m³").dimension == "density"
    assert normalize_value("0.03 g/cm³").value == Decimal(30)
    assert normalize_value("100°").dimension == "angle"
    assert normalize_value("30 nights").value == Decimal(30 * 86400)

    opaque = Constraint(
        "throughput", "at least 10 widgets", "ge", 10, "Widgets"
    )
    assert constraint_matches(
        opaque, CandidateFact("throughput", "known", 12, "widgets")
    )
    with pytest.raises(UnitNormalizationError, match="incompatible"):
        constraint_matches(
            opaque, CandidateFact("throughput", "known", 12, "gadgets")
        )


def test_bare_dollar_is_not_silently_usd():
    with pytest.raises(UnitNormalizationError, match="unsupported unit"):
        normalize_value("$100")
    resolved = normalize_value("$100", "USD")
    assert resolved.dimension == "currency:USD"
    assert resolved.value == Decimal(100)

    opaque_dollars = Constraint("price", "under $100", "lt", "$100")
    assert constraint_matches(
        opaque_dollars, CandidateFact("price", "known", "$90")
    )
    with pytest.raises(UnitNormalizationError, match="incompatible"):
        constraint_matches(
            opaque_dollars,
            CandidateFact("price", "known", 90, "USD"),
        )


def test_same_origin_uses_scheme_host_and_effective_port():
    assert same_origin("/item/a", "https://example.test/start")
    assert same_origin("https://example.test:443/item/a", ORIGIN)
    assert not same_origin("http://example.test/item/a", ORIGIN)
    assert not same_origin("https://other.test/item/a", ORIGIN)


def test_selection_uses_pareto_then_normalized_minimax_regret():
    rows = (
        _candidate("balanced", mass=2, runtime=8),
        _candidate("light", mass=1, runtime=1),
        _candidate("slow-heavy", mass=3, runtime=2),
    )
    result = select_candidate(
        _contract(), reversed(rows), start_origin=ORIGIN
    )
    assert result.selected_candidate_id == "balanced"
    assert result.method == "normalized_minimax_regret"
    assert result.pareto_frontier_ids == ("balanced", "light")
    assert "slow-heavy" not in result.pareto_frontier_ids


def test_dominated_irrelevant_candidate_cannot_change_frontier_ranking():
    contract = TaskContract(
        "Maximize both values.",
        objectives=(
            Objective("x", "maximize x", "maximize", "count"),
            Objective("y", "maximize y", "maximize", "count"),
        ),
    )

    def row(candidate_id, x, y):
        return Candidate(
            candidate_id,
            candidate_id,
            f"{ORIGIN}/{candidate_id}",
            (
                CandidateFact("x", "known", x, "count"),
                CandidateFact("y", "known", y, "count"),
            ),
        )

    relevant = (row("a", 8, 5), row("b", 5, 8))
    before = select_candidate(contract, relevant, start_origin=ORIGIN)
    after = select_candidate(
        contract, (*relevant, row("c", 0, 5)), start_origin=ORIGIN
    )
    assert before.selected_candidate_id == "a"
    assert after.selected_candidate_id == "a"
    assert after.pareto_frontier_ids == ("a", "b")
    assert set(after.utilities) == {"a", "b"}


def test_explicit_literal_priority_precedes_unstated_balancing():
    rows = (
        _candidate("balanced", mass=2, runtime=8),
        _candidate("light", mass=1, runtime=1),
    )
    result = select_candidate(
        _contract(priorities=True), rows, start_origin=ORIGIN
    )
    assert result.selected_candidate_id == "light"
    assert result.method == "explicit_preference"


def test_explicit_literal_weights_are_applied_within_priority_level():
    contract = TaskContract(
        "Prefer low mass three times as much as runtime.",
        objectives=(
            Objective(
                "mass", "minimize mass", "minimize", "kg", weight=3
            ),
            Objective(
                "runtime", "maximize runtime", "maximize", "h", weight=1
            ),
        ),
    )
    rows = (
        Candidate(
            "light",
            "Light",
            f"{ORIGIN}/light",
            (
                CandidateFact("mass", "known", 1, "kg"),
                CandidateFact("runtime", "known", 1, "h"),
            ),
        ),
        Candidate(
            "long",
            "Long",
            f"{ORIGIN}/long",
            (
                CandidateFact("mass", "known", 2, "kg"),
                CandidateFact("runtime", "known", 10, "h"),
            ),
        ),
    )
    result = select_candidate(contract, rows, start_origin=ORIGIN)
    assert result.selected_candidate_id == "light"
    assert result.method == "explicit_preference"


def test_objective_dimensions_must_be_compatible_even_without_contract_unit():
    contract = TaskContract(
        "Choose the smallest value.",
        objectives=(Objective("size", "minimize size", "minimize"),),
    )
    rows = (
        Candidate(
            "mass",
            "Mass",
            f"{ORIGIN}/mass",
            (CandidateFact("size", "known", 2, "kg"),),
        ),
        Candidate(
            "time",
            "Time",
            f"{ORIGIN}/time",
            (CandidateFact("size", "known", 1, "h"),),
        ),
    )
    result = select_candidate(contract, rows, start_origin=ORIGIN)
    assert result.selected_candidate_id is None
    assert all(
        "incompatible dimensions" in reasons[0]
        for reasons in result.rejected_candidates.values()
    )


def test_checkpoint_requires_exact_best_available_frontier():
    candidate = _candidate("a")
    contract = _contract()
    for frontier, fragment in (
        (
            _frontier(
                2,
                advertised_count=None,
                excluded_count=0,
                unresolved_count=0,
            ),
            "inspected_count must equal",
        ),
        (
            _frontier(1, advertised_count=None),
            "requires advertised_count",
        ),
        (_frontier(1, exhausted=False), "exhausted frontier"),
        (
            _frontier(
                2,
                advertised_count=2,
                unresolved_count=1,
            ),
            "unresolved_count=0",
        ),
        (
            _frontier(1, advertised_count=2),
            "advertised_count must equal",
        ),
    ):
        result = evaluate_checkpoint(
            contract=contract,
            start_origin=ORIGIN,
            current_url=f"{ORIGIN}/search",
            rendered_page_text=frontier.basis,
            frontier=frontier,
            candidates=(candidate,),
            proposed_candidate_id="a",
        )
        assert not result.approved
        assert fragment in " ".join(result.reasons)


def test_checkpoint_rejects_submitted_pareto_dominated_candidate():
    best = _candidate("best", mass=1, runtime=10)
    dominated = _candidate("dominated", mass=2, runtime=8)
    frontier = _frontier(2)
    result = evaluate_checkpoint(
        contract=_contract(),
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search",
        rendered_page_text=frontier.basis,
        frontier=frontier,
        candidates=(best, dominated),
        proposed_candidate_id="best",
    )
    assert not result.approved
    assert "exactly the nondominated feasible frontier" in " ".join(
        result.reasons
    )


def test_checkpoint_requires_current_same_origin_count_quote():
    candidate = _candidate("a")
    contract = _contract()
    frontier = _frontier(2112)
    for current_url, page_text, basis, fragment in (
        (
            "https://other.test/search",
            frontier.basis,
            frontier.basis,
            "outside the assigned origin",
        ),
        (
            f"{ORIGIN}/search",
            "No count is rendered here",
            frontier.basis,
            "exact quote",
        ),
        (
            f"{ORIGIN}/search",
            "Showing the complete catalog",
            "Showing the complete catalog",
            "include advertised_count",
        ),
    ):
        submitted = Frontier(
            inspected_count=2112,
            advertised_count=2112,
            excluded_count=2111,
            unresolved_count=0,
            exhausted=True,
            basis=basis,
        )
        result = evaluate_checkpoint(
            contract=contract,
            start_origin=ORIGIN,
            current_url=current_url,
            rendered_page_text=page_text,
            frontier=submitted,
            candidates=(candidate,),
            proposed_candidate_id="a",
        )
        assert not result.approved
        assert fragment in " ".join(result.reasons)

    quoted = Frontier(
        inspected_count=2112,
        advertised_count=2112,
        excluded_count=2111,
        unresolved_count=0,
        exhausted=True,
        basis="1–24 of 2,112 results",
    )
    approved = evaluate_checkpoint(
        contract=contract,
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search?page=88",
        rendered_page_text=f"Search\n{quoted.basis}\nEnd",
        frontier=quoted,
        candidates=(candidate,),
        proposed_candidate_id="a",
    )
    assert approved.approved


def test_checkpoint_accepts_complete_finite_numbered_pages_without_item_total():
    candidate = _candidate("a")
    pager = "Page: " + " ".join(str(page) for page in range(1, 89))
    frontier = Frontier(
        inspected_count=2112,
        advertised_count=None,
        coverage_mode=CoverageMode.FINITE_PAGES,
        advertised_page_count=88,
        enumerated_page_count=88,
        excluded_count=2111,
        unresolved_count=0,
        exhausted=True,
        basis=pager,
    )
    result = evaluate_checkpoint(
        contract=_contract(),
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search?page=88",
        rendered_page_text=f"Results\n{pager}\nEnd",
        frontier=frontier,
        candidates=(candidate,),
        proposed_candidate_id="a",
    )
    assert result.approved


def test_finite_page_witness_keeps_99_and_100_as_separate_labels():
    pager = "Page: " + " ".join(str(page) for page in range(1, 101))
    frontier = Frontier(
        inspected_count=1,
        advertised_count=None,
        coverage_mode="finite_pages",
        advertised_page_count=100,
        enumerated_page_count=100,
        excluded_count=0,
        unresolved_count=0,
        exhausted=True,
        basis=pager,
    )
    result = evaluate_checkpoint(
        contract=_contract(),
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search?page=100",
        rendered_page_text=pager,
        frontier=frontier,
        candidates=(_candidate("a"),),
        proposed_candidate_id="a",
    )
    assert result.approved


def test_finite_page_witness_accepts_formatted_thousandth_page():
    labels = [str(page) for page in range(1, 1000)] + ["1,000"]
    pager = "Page: " + " ".join(labels)
    frontier = Frontier(
        inspected_count=1,
        advertised_count=None,
        coverage_mode="finite_pages",
        advertised_page_count=1000,
        enumerated_page_count=1000,
        excluded_count=0,
        unresolved_count=0,
        exhausted=True,
        basis=pager,
    )
    result = evaluate_checkpoint(
        contract=_contract(),
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search?page=1000",
        rendered_page_text=pager,
        frontier=frontier,
        candidates=(_candidate("a"),),
        proposed_candidate_id="a",
    )
    assert result.approved


def test_finite_page_witness_rejects_prefix_of_larger_rendered_pager():
    full_pager = "Page: " + " ".join(
        str(page) for page in range(1, 89)
    )
    frontier = Frontier(
        inspected_count=1,
        advertised_count=None,
        coverage_mode="finite_pages",
        advertised_page_count=1,
        enumerated_page_count=1,
        excluded_count=0,
        unresolved_count=0,
        exhausted=True,
        basis="Page: 1",
    )
    result = evaluate_checkpoint(
        contract=_contract(),
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search",
        rendered_page_text=full_pager,
        frontier=frontier,
        candidates=(_candidate("a"),),
        proposed_candidate_id="a",
    )
    assert not result.approved
    assert "complete rendered line" in " ".join(result.reasons)


@pytest.mark.parametrize(
    ("pager", "advertised_pages", "enumerated_pages", "fragment"),
    (
        ("Page: 1 2 4", 4, 4, "every page integer"),
        ("Page: 1 2 2 3", 3, 3, "every page integer"),
        ("Page: 2 1 3", 3, 3, "every page integer"),
        ("Page: 1 2 … 4", 4, 4, "every page integer"),
        ("88 reviews", 88, 88, "every page integer"),
        ("Page: -1 -2", 2, 2, "every page integer"),
        ("Page: 1.0 2.0", 2, 2, "every page integer"),
        ("Page: 1 2 3 Next", 3, 3, "every page integer"),
        (
            "Page: 1 2 3",
            3,
            2,
            "enumerated_page_count must equal",
        ),
    ),
)
def test_checkpoint_rejects_incomplete_finite_page_witnesses(
    pager, advertised_pages, enumerated_pages, fragment
):
    frontier = Frontier(
        inspected_count=1,
        advertised_count=None,
        coverage_mode="finite_pages",
        advertised_page_count=advertised_pages,
        enumerated_page_count=enumerated_pages,
        excluded_count=0,
        unresolved_count=0,
        exhausted=True,
        basis=pager,
    )
    result = evaluate_checkpoint(
        contract=_contract(),
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search",
        rendered_page_text=pager,
        frontier=frontier,
        candidates=(_candidate("a"),),
        proposed_candidate_id="a",
    )
    assert not result.approved
    assert fragment in " ".join(result.reasons)


def test_checkpoint_coverage_modes_are_mutually_exclusive():
    candidate = _candidate("a")
    mixed_total = _frontier(
        1,
        advertised_page_count=1,
        enumerated_page_count=1,
    )
    mixed_pages = Frontier(
        inspected_count=1,
        advertised_count=1,
        coverage_mode="finite_pages",
        advertised_page_count=1,
        enumerated_page_count=1,
        excluded_count=0,
        unresolved_count=0,
        exhausted=True,
        basis="Page: 1",
    )
    for frontier, fragment in (
        (mixed_total, "advertised_total forbids"),
        (mixed_pages, "finite_pages forbids advertised_count"),
    ):
        result = evaluate_checkpoint(
            contract=_contract(),
            start_origin=ORIGIN,
            current_url=f"{ORIGIN}/search",
            rendered_page_text=frontier.basis,
            frontier=frontier,
            candidates=(candidate,),
            proposed_candidate_id="a",
        )
        assert not result.approved
        assert fragment in " ".join(result.reasons)


def test_finite_pages_keeps_common_coverage_guards():
    candidate = _candidate("a")
    base = {
        "inspected_count": 1,
        "advertised_count": None,
        "coverage_mode": "finite_pages",
        "advertised_page_count": 1,
        "enumerated_page_count": 1,
        "excluded_count": 0,
        "unresolved_count": 0,
        "exhausted": True,
        "basis": "Page: 1",
    }
    cases = (
        ({"exhausted": False}, "exhausted frontier"),
        (
            {"inspected_count": 2, "unresolved_count": 1},
            "unresolved_count=0",
        ),
        (
            {"inspected_count": 2, "excluded_count": 0},
            "inspected_count must equal",
        ),
    )
    for updates, fragment in cases:
        values = {**base, **updates}
        frontier = Frontier(**values)
        result = evaluate_checkpoint(
            contract=_contract(),
            start_origin=ORIGIN,
            current_url=f"{ORIGIN}/search",
            rendered_page_text=frontier.basis,
            frontier=frontier,
            candidates=(candidate,),
            proposed_candidate_id="a",
        )
        assert not result.approved
        assert fragment in " ".join(result.reasons)


def test_checkpoint_rejects_wrong_origin_and_nonexact_or_unknown_facts():
    contract = _contract()
    wrong_origin = _candidate("outside", origin="https://other.test")
    missing = Candidate(
        "missing",
        "missing",
        f"{ORIGIN}/missing",
        (
            CandidateFact("cost", "known", 90, "USD"),
            CandidateFact("mass", "known", 2, "kg"),
        ),
    )
    unknown = Candidate(
        "unknown",
        "unknown",
        f"{ORIGIN}/unknown",
        (
            CandidateFact("cost", "known", 90, "USD"),
            CandidateFact("mass", "unknown"),
            CandidateFact("runtime", "known", 8, "h"),
        ),
    )
    result = evaluate_checkpoint(
        contract=contract,
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search",
        rendered_page_text=_frontier(3).basis,
        frontier=_frontier(3),
        candidates=(wrong_origin, missing, unknown),
        proposed_candidate_id="unknown",
    )
    assert not result.approved
    assert "outside the assigned origin" in str(result.rejected_candidates)
    assert "missing criteria" in str(result.rejected_candidates)
    assert "is unknown" in str(result.rejected_candidates)


def test_hard_constraint_failures_must_be_counted_as_excluded():
    contract = _contract()
    good = _candidate("good", cost=80)
    bad = _candidate("bad", cost=120)
    rejected = evaluate_checkpoint(
        contract=contract,
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search",
        rendered_page_text=_frontier(2).basis,
        frontier=_frontier(2),
        candidates=(good, bad),
        proposed_candidate_id="good",
    )
    assert not rejected.approved
    assert rejected.rejected_candidates["bad"] == (
        "constraint cost is not satisfied",
    )

    approved = evaluate_checkpoint(
        contract=contract,
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search",
        rendered_page_text=_frontier(2, excluded_count=1).basis,
        frontier=_frontier(2, excluded_count=1),
        candidates=(good,),
        proposed_candidate_id="good",
    )
    assert approved.approved


def test_any_exact_best_tie_is_approved_without_candidate_id_control():
    contract = TaskContract(
        "Choose the lightest.",
        objectives=(Objective("mass", "minimize mass", "minimize", "kg"),),
    )
    rows = (
        Candidate(
            "b",
            "B",
            f"{ORIGIN}/b",
            (CandidateFact("mass", "known", 1, "kg"),),
        ),
        Candidate(
            "a",
            "A",
            f"{ORIGIN}/a",
            (CandidateFact("mass", "known", 1, "kg"),),
        ),
    )
    frontier = _frontier(3, excluded_count=1)
    tied = evaluate_checkpoint(
        contract=contract,
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search",
        rendered_page_text=frontier.basis,
        frontier=frontier,
        candidates=rows,
        proposed_candidate_id="b",
    )
    assert tied.approved
    assert tied.selected_candidate_id == "b"
    assert tied.tied_candidate_ids == ("a", "b")

    worse = evaluate_checkpoint(
        contract=contract,
        start_origin=ORIGIN,
        current_url=f"{ORIGIN}/search",
        rendered_page_text=_frontier(3).basis,
        frontier=_frontier(3),
        candidates=rows,
        proposed_candidate_id="c",
    )
    assert not worse.approved
    assert worse.selected_candidate_id == "a"
    assert "exact best candidates" in " ".join(worse.reasons)


def test_frontier_counts_are_strict_integers():
    for value in (True, 1.0, "1"):
        with pytest.raises(ValueError):
            Frontier(
                inspected_count=value,
                advertised_count=1,
                excluded_count=0,
                unresolved_count=0,
                exhausted=True,
                basis="1 result",
            )


def test_frontier_page_counts_are_strict_positive_integers():
    for name in ("advertised_page_count", "enumerated_page_count"):
        for value in (True, 1.0, "1", 0, -1):
            values = {
                "inspected_count": 1,
                "advertised_count": None,
                "coverage_mode": "finite_pages",
                "advertised_page_count": 1,
                "enumerated_page_count": 1,
                "excluded_count": 0,
                "unresolved_count": 0,
                "exhausted": True,
                "basis": "Page: 1",
                name: value,
            }
            with pytest.raises(ValueError, match=name):
                Frontier(**values)


def test_frontier_exhaustion_is_strictly_boolean():
    for value in (1, 0, "yes", None):
        with pytest.raises(ValueError, match="exhausted must be a boolean"):
            Frontier(
                inspected_count=1,
                advertised_count=1,
                excluded_count=0,
                unresolved_count=0,
                exhausted=value,
                basis="1 result",
            )


def test_canonical_hash_is_order_stable():
    assert canonical_json({"b": 2, "a": Decimal("1.00")}) == (
        '{"a":"1","b":2}'
    )
    assert stable_hash({"a": 1, "b": 2}) == stable_hash(
        {"b": 2, "a": 1}
    )
