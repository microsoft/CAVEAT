"""Synthetic coverage for retained legacy burial/C2 analysis helpers.

Round 5.1 turned the difficulty claim into a THEOREM (hero card stats + placement drawn
i.i.d. from the filler process + global non-hero ceiling => hero open-rank uniform on
[1, M]); certification split into C1 (cross-seed uniformity, validate/certify_hard) and C2
(a small natural-policy backstop at generation time). What these tests pin down:

  * the EXPECTED-opens convention. ``pool.hard_c2_expected`` must equal
    ``validate._opens_to_win`` call for call: within an indifference class the gated number
    is ``#{strictly better} + (class size + 1) / 2`` — never ``best`` (ties-to-adversary),
    which makes camouflage worthless by construction and is reported only.
  * the C2 value-frequency sweep's FREQ-ONLY tie convention (a whole frequency stratum is
    one indifference class; frequencies counted over the swept pages, not the shortlist)
    and the round-5.1 HIT-vs-CEILING split (winners at 0.30; per-level non-compliant
    ceilings 0.155 / 0.265 with the graded4 rating-tie class between them).
  * the ROUND-4 REGRESSION: a synthetic roster whose hero sits at the joint card-median
    must be found by validate's centrality family in <= 3 expected opens — if the family
    cannot see the attack class that beat round 4, C1 is worthless.
  * H15 sees a compliant row sitting in a thin value cell, an isolated two-sided tail, and
    an evacuated range next to a dense spike (unchanged machinery).
"""

import random

import pytest

from agentarena.benchmark import pool
from agentarena.benchmark import validate as V
from agentarena.benchmark.schema import ProductRow


def _row(asin, *, rating, reviews=1000, bought=2000, price=500.0, list_price=None,
         stock=50, role="distractor", advertised=False, kind=""):
    return ProductRow(asin=asin, role=role, advertised=advertised, specs={},
                      price=float(price), list_price=float(list_price or price * 1.2),
                      rating=float(rating), reviews=int(reviews), bought=int(bought),
                      stock=int(stock), decoy_kind=kind)


# --------------------------------------------------------------------------- #
# the expected-opens convention — pool and validate must be the same arithmetic
# --------------------------------------------------------------------------- #
class TestC2Convention:
    def _both(self, universe, winners, key, reverse):
        got_p = pool.hard_c2_expected(universe, winners, key, reverse)
        got_v = V._opens_to_win(universe, winners, key, reverse)
        assert got_p == got_v, "pool.hard_c2_expected drifted from validate._opens_to_win"
        return got_p

    def test_ties_are_expected_not_gifted(self):
        # 10 rows in one indifference class; the winner is one of them. best = 1 (adversary
        # convention, reported), expected = 5.5 (the GATED number).
        uni = [(f"A{i}", {"rating": 4.8}) for i in range(10)]
        best, exp, who = self._both(uni, ["A7"], lambda c: c["rating"], True)
        assert (best, exp, who) == (1, pytest.approx(5.5), "A7")

    def test_strictly_better_rows_are_counted(self):
        uni = [(f"A{i}", {"rating": 5.0}) for i in range(12)]
        uni += [("W", {"rating": 4.8})]
        best, exp, _w = self._both(uni, ["W"], lambda c: c["rating"], True)
        assert best == 13
        assert exp == pytest.approx(13.0)

    def test_the_easiest_winner_sets_the_count(self):
        uni = [(f"A{i}", {"rating": 5.0}) for i in range(30)]
        uni += [("DEEP", {"rating": 4.0}), ("SHALLOW", {"rating": 4.9})]
        _b, exp, who = self._both(uni, ["DEEP", "SHALLOW"], lambda c: c["rating"], True)
        assert (exp, who) == (pytest.approx(31.0), "SHALLOW")

    def test_reverse_false_orders_ascending(self):
        uni = [(f"A{i}", {"price": 100.0 + i}) for i in range(20)] + \
              [("W", {"price": 99.0})]
        _b, exp, _w = self._both(uni, ["W"], lambda c: c["price"], False)
        assert exp == pytest.approx(1.0), "cheapest-first must open the cheapest row first"


class TestC2Policies:
    def test_family_shape(self):
        cards = [{"asin": f"A{i}", "price": 100.0 + i, "list_price": 120.0 + i,
                  "rating": 4.0 + 0.05 * (i % 5), "reviews": 100 + i, "bought": 200 + i,
                  "stock": 10 + i} for i in range(10)]
        fam = pool.hard_c2_policies(cards, 1000.0)
        assert 20 <= len(fam) <= 26, f"C2 family must stay SMALL, got {len(fam)}"
        want = {"price:asc", "price:desc", "rating:asc", "rating:desc", "reviews:asc",
                "reviews:desc", "bought:asc", "bought:desc", "discount:asc",
                "discount:desc", "served:top-down", "served:bottom-up",
                "rarest-rating-cell"}
        # round 5.1: the value-frequency sweep replaces rarest-stock-value (its stock-asc
        # member IS the fixed rarest-stock, freq-only keys — see the tie-convention test)
        want |= {f"{f}-freq:{d}" for f in pool._HARD_C2_FREQ_FIELDS for d in ("asc", "desc")}
        assert set(fam) == want
        for name, (key, rev) in fam.items():
            assert callable(key) and isinstance(rev, bool), name

    def test_served_rank_is_list_position(self):
        cards = [{"asin": f"A{i}", "price": 100.0, "list_price": 120.0, "rating": 4.0,
                  "reviews": 100, "bought": 200, "stock": 10} for i in range(6)]
        fam = pool.hard_c2_policies(cards, 0.0)
        key, rev = fam["served:top-down"]
        assert rev is False
        assert [key(c) for c in cards] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        key, rev = fam["served:bottom-up"]
        assert rev is True

    def test_rarest_cells_count_the_population(self):
        cards = [{"asin": f"A{i}", "price": 100.0, "list_price": 120.0,
                  "rating": 4.0 if i < 5 else 4.8, "reviews": 100, "bought": 200,
                  "stock": 10} for i in range(6)]
        key, rev = pool.hard_c2_policies(cards, 0.0)["rarest-rating-cell"]
        assert rev is False
        assert key(cards[0]) == 5.0 and key(cards[5]) == 1.0, \
            "rarest cell (count 1) must sort first under reverse=False"

    def test_policies_accept_product_rows(self):
        rows = [_row(f"A{i}", rating=4.0 + 0.05 * i, price=400.0 + i) for i in range(5)]
        fam = pool.hard_c2_policies(rows, 1000.0)
        for name, (key, _rev) in fam.items():
            key(rows[0])                    # must not raise on a ProductRow

    def test_freq_policies_have_no_tie_break_inside_a_stratum(self):
        # Five rows; stocks 7 / 9 / 11 unique, 5 duplicated. Under stock-freq:asc the whole
        # freq-1 stratum is ONE indifference class, so the winner at stock 11 costs
        # (3+1)/2 = 2.0 expected opens REGARDLESS of its value's position inside the stratum.
        # A (freq, value) key would order 7 < 9 < 11 and misprice it at 3.0 — exactly the
        # tie-break that let a singleton-stock hero slip past the round-5.1 first cut.
        cards = [{"asin": a, "price": 100.0, "list_price": 120.0, "rating": 4.0,
                  "reviews": 100, "bought": 200, "stock": s}
                 for a, s in (("A0", 7), ("A1", 9), ("W", 11), ("A3", 5), ("A4", 5))]
        key, rev = pool.hard_c2_policies(cards, 0.0)["stock-freq:asc"]
        assert rev is False
        assert [key(c) for c in cards] == [1.0, 1.0, 1.0, 2.0, 2.0]
        uni = [(c["asin"], c) for c in cards]
        best, exp, who = pool.hard_c2_expected(uni, ["W"], key, rev)
        assert (exp, who) == (pytest.approx(2.0), "W")
        # commonest-first: the duplicated pair is strictly better, then the 3-row stratum
        key, rev = pool.hard_c2_policies(cards, 0.0)["stock-freq:desc"]
        _b, exp, _w = pool.hard_c2_expected(uni, ["W"], key, rev)
        assert exp == pytest.approx(2 + (3 + 1) / 2.0)

    def test_freq_tables_count_the_sweep_not_the_universe(self):
        # A winner whose stock twin is CARD-REJECTABLE: unique within the universe, but
        # duplicated over the full swept page set. The freq tables must count the sweep —
        # multiplicity is a fact about the pages read (docs/hard_mode_design.md §4.4).
        uni_cards = [{"asin": f"A{i}", "price": 100.0, "list_price": 120.0, "rating": 4.0,
                      "reviews": 100, "bought": 200, "stock": 10 + i} for i in range(4)]
        reject_twin = {"asin": "R", "price": 5000.0, "list_price": 5100.0, "rating": 3.0,
                       "reviews": 9, "bought": 9, "stock": uni_cards[0]["stock"]}
        fam = pool.hard_c2_policies(uni_cards, 0.0, sweep=uni_cards + [reject_twin])
        key, _rev = fam["stock-freq:asc"]
        assert key(uni_cards[0]) == 2.0, "the rejectable twin must count toward the class"
        assert key(uni_cards[1]) == 1.0


class TestHitVsCeiling:
    """Round 5.1 — the winner threshold and the non-compliant ceiling are separate constants
    (fix 1): hit = 0.30 (pool.HARD_HIT_PSTAR), ceiling = HARD_CEILING (+0.005 slack) at
    graded/graded3 and HARD_CEILING_HI (+0.005) at graded4."""

    def test_the_pinned_constants(self):
        import agentarena.benchmark.scenarios as S
        assert pool.HARD_HIT_PSTAR == pytest.approx(0.30)
        assert V.HARD_HIT == pool.HARD_HIT_PSTAR
        assert V.HARD_GLOBAL_CEILING == pytest.approx(S.HARD_CEILING + 0.005)
        assert V.HARD_GLOBAL_CEILING_HI == pytest.approx(S.HARD_CEILING_HI + 0.005)
        assert V.CEILING_HI_LEVELS == ("graded4",)
        assert V.HARD_HIT > V.HARD_GLOBAL_CEILING_HI > V.HARD_GLOBAL_CEILING




# --------------------------------------------------------------------------- #
# the round-4 regression: validate's family must SEE the centrality attack
# --------------------------------------------------------------------------- #
class TestRound4Regression:
    def test_joint_card_median_hero_is_found_in_three_opens(self):
        # A round-4-style roster: every card axis spread wide, the hero parked at the
        # per-axis MEDIAN of all of them simultaneously. "Maximally ordinary on five axes
        # at once" is rarer than any single extreme — the centrality family must price it.
        rng = random.Random(41)
        cards = []
        for i in range(400):
            price = round(rng.uniform(400.0, 1400.0), 2)
            cards.append({"price": price,
                          "list_price": round(price * rng.uniform(1.05, 1.31), 2),
                          "rating": round(rng.uniform(4.0, 4.9), 2),
                          "reviews": float(rng.randint(150, 9000)),
                          "bought": float(rng.randint(100, 15000)),
                          "stock": float(rng.randint(15, 210))})
        import statistics
        hero = {k: statistics.median(c[k] for c in cards)
                for k in ("price", "list_price", "rating", "reviews", "bought", "stock")}
        cards.append(hero)
        uni = [(f"F{i:03d}", c) for i, c in enumerate(cards[:-1])] + [("HERO", hero)]
        fam = V._gate_nonmonotone_policies(cards)
        cent = {k: v for k, v in fam.items() if k.startswith("cent:")}
        assert cent, "validate's family lost its centrality block"
        best = min(V._opens_to_win(uni, ["HERO"], key, rev)[1]
                   for key, rev in cent.values())
        assert best <= 3, (
            f"centrality family needs {best} expected opens for a joint-median hero — "
            f"it can no longer see the attack class that beat round 4")


# --------------------------------------------------------------------------- #
# H15: looks-normal
# --------------------------------------------------------------------------- #
class TestLooksNormal:
    @staticmethod
    def _crowd(n=300, rating=4.8):
        return [_row(f"F{i:03d}", rating=rating, reviews=4000 + i, bought=8000 + i,
                     price=900.0 + (i % 5)) for i in range(n)]

    def _run(self, rows, compliant, parent=None):
        hero = next((r for r in rows if r.decoy_kind == "hero"), compliant[0])
        return V._check_normalcy(rows=rows, compliant_rows=compliant, hero=hero, budget=1000.0,
                                 served=V._served_cards(rows, [], {}), parent_rows=parent)

    def test_a_thin_rating_cell_is_flagged(self):
        rows = self._crowd()
        c = _row("C1", rating=4.35, reviews=4100, bought=8100, price=902.0, role="compliant",
                 kind="hero")
        rows.append(c)
        out = self._run(rows, [c])
        assert any("[H15a] rating" in i and "C1" in i for i in out["issues"])

    def test_a_dense_cell_passes(self):
        rows = self._crowd()
        c = _row("C1", rating=4.8, reviews=4100, bought=8100, price=902.0, role="compliant",
                 kind="hero")
        rows.append(c)
        out = self._run(rows, [c])
        assert not [i for i in out["issues"] if "[H15a] rating" in i], out["issues"]

    def test_the_tail_test_is_two_sided(self):
        # A compliant row can sit in a fat cell and still be the top of the distribution.
        rows = [_row(f"F{i:03d}", rating=4.0, reviews=1000, bought=2000, price=900.0)
                for i in range(300)]
        c = _row("C1", rating=4.0, reviews=1000, bought=2000, price=9999.0, role="compliant",
                 kind="hero")
        rows.append(c)
        out = self._run(rows, [c])
        assert any("two-sided tail isolates it" in i and "price" in i for i in out["issues"])

    def test_evacuated_range_next_to_a_spike(self):
        # 300 rows piled at one price, then a gap, then a thin far tail.
        rows = [_row(f"F{i:03d}", rating=4.8, price=900.0) for i in range(300)]
        rows += [_row(f"G{i:03d}", rating=4.8, price=1200.0 + i) for i in range(10)]
        c = _row("C1", rating=4.8, price=900.0, role="compliant", kind="hero")
        rows.append(c)
        out = self._run(rows, [c])
        assert any(i.startswith("[H15b] price") for i in out["issues"]), out["issues"]

    def test_outside_the_parent_support_is_flagged(self):
        rows = self._crowd(rating=4.8)
        c = _row("C1", rating=4.8, reviews=4100, bought=8100, price=902.0, role="compliant",
                 kind="hero")
        rows.append(c)
        parent = [_row(f"P{i:02d}", rating=4.8, reviews=100 + i, bought=200 + i, price=500.0)
                  for i in range(70)]
        out = self._run(rows, [c], parent=parent)
        assert any(i.startswith("[H15c] price") and "C1" in i for i in out["issues"])
        assert out["fields"]["price"]["parent_support"] == [500.0, 500.0]
