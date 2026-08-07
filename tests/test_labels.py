"""Label-construction tests. These encode the bugs found while building, so they stay fixed."""

from __future__ import annotations

import pandas as pd
import pytest

from drawdown_radar.config import (
    next_quarter,
    quarter_index,
    quarter_range,
    quarter_to_repdte,
    repdte_to_quarter,
)
from drawdown_radar.labels import _clean_id, build_labels, merger_events


def _panel(rows: list[dict]) -> pd.DataFrame:
    """Minimal financials panel. Deposits are in $ thousands, as FDIC reports them."""
    base = {"NAME": "TEST BANK", "BKCLASS": "NM", "STALP": "CA", "ASSET": 1_000_000, "ACTEVT": None}
    return pd.DataFrame([{**base, **r} for r in rows])


def _inst(rows: list[dict]) -> pd.DataFrame:
    base = {
        "NAME": "TEST BANK",
        "NAMEHCR": "",
        "RSSDHCR": "",
        "ESTYMD": "01/01/1950",
        "ENDEFYMD": None,
        "NEWCERT": None,
        "ACTIVE": 1,
        "BKCLASS": "NM",
        "ASSET": 1_000_000,
        "STALP": "CA",
        "CITY": "X",
    }
    return pd.DataFrame([{**base, **r} for r in rows])


# --------------------------------------------------------------- quarter helpers
def test_quarter_roundtrip():
    for q in ["2015Q1", "2019Q3", "2026Q1"]:
        assert repdte_to_quarter(quarter_to_repdte(q)) == q


def test_quarter_index_is_monotonic_across_year_boundary():
    assert quarter_index("2015Q4") + 1 == quarter_index("2016Q1")
    assert next_quarter("2015Q4") == "2016Q1"


def test_quarter_range_inclusive():
    r = quarter_range("2015Q3", "2016Q2")
    assert r == ["2015Q3", "2015Q4", "2016Q1", "2016Q2"]


# --------------------------------------------------------------- the sentinel bug
def test_clean_id_treats_empty_string_as_missing():
    """RSSDHCR uses '' for 'no holding company'. Comparing raw values made every pair of
    independent banks look like affiliates of the same parent."""
    out = _clean_id(pd.Series(["", "  ", "nan", "0", "1234"]))
    assert out.isna().tolist() == [True, True, True, True, False]
    assert out.iloc[4] == "1234"


def test_independent_banks_are_not_treated_as_same_holding_company():
    inst = _inst(
        [
            {"CERT": 1, "ACTIVE": 0, "ENDEFYMD": "06/30/2019", "NEWCERT": 2, "RSSDHCR": ""},
            {"CERT": 2, "RSSDHCR": ""},
        ]
    )
    exits, _ = merger_events(inst)
    assert not exits.loc[exits["cert"] == 1, "same_hc_successor"].iloc[0]


def test_true_affiliate_consolidation_is_detected():
    inst = _inst(
        [
            {"CERT": 1, "ACTIVE": 0, "ENDEFYMD": "06/30/2019", "NEWCERT": 2, "RSSDHCR": "999"},
            {"CERT": 2, "RSSDHCR": "999"},
        ]
    )
    exits, _ = merger_events(inst)
    assert exits.loc[exits["cert"] == 1, "same_hc_successor"].iloc[0]


# --------------------------------------------------------------- adjacency
def test_gap_in_history_does_not_produce_a_two_quarter_change():
    """A missing 2017Q1 must NOT make 2016Q4 -> 2017Q2 look like a one-quarter change."""
    panel = _panel(
        [
            {"CERT": 5, "REPDTE": "20160331", "quarter": "2016Q1", "DEP": 100_000},
            {"CERT": 5, "REPDTE": "20160630", "quarter": "2016Q2", "DEP": 100_000},
            {"CERT": 5, "REPDTE": "20160930", "quarter": "2016Q3", "DEP": 100_000},
            {"CERT": 5, "REPDTE": "20161231", "quarter": "2016Q4", "DEP": 100_000},
            # 2017Q1 deliberately absent
            {"CERT": 5, "REPDTE": "20170630", "quarter": "2017Q2", "DEP": 50_000},
        ]
    )
    labelled, _ = build_labels(panel, _inst([{"CERT": 5}]))
    assert "2016Q4" not in set(labelled["quarter"]), "gap row must be dropped, not bridged"


def _quarters(cert: int, deps: list[int], start: str = "2016Q1") -> list[dict]:
    """Consecutive quarterly rows for one bank, starting at `start`.

    A row is only labellable once the bank has MIN_HISTORY_QUARTERS (5) observations AND a
    T+1 row exists, so fixtures need 6 quarters to yield a single labelled row.
    """
    qs = quarter_range(start, "2030Q4")[: len(deps)]
    return [
        {"CERT": cert, "REPDTE": quarter_to_repdte(q), "quarter": q, "DEP": d}
        for q, d in zip(qs, deps, strict=True)
    ]


def test_threshold_is_inclusive_at_exactly_minus_five_percent():
    # 5 flat quarters of history, then a -5.0% move into the 6th.
    panel = _panel(_quarters(7, [100_000] * 5 + [95_000]))
    labelled, _ = build_labels(panel, _inst([{"CERT": 7}]))
    row = labelled[labelled["quarter"] == "2017Q1"]
    assert len(row) == 1, "the 5th observation with a T+1 row must be labellable"
    assert row["dep_growth_next"].iloc[0] == pytest.approx(-0.05)
    assert row["label"].iloc[0] == 1, "-5.0% exactly must count as an event"


def test_row_just_short_of_minimum_history_is_excluded():
    """Boundary: the 4th observation is never labelled even if a T+1 row exists."""
    panel = _panel(_quarters(8, [100_000] * 4 + [90_000]))
    labelled, _ = build_labels(panel, _inst([{"CERT": 8}]))
    assert "2016Q4" not in set(labelled["quarter"])


# --------------------------------------------------------------- exclusions
def test_foreign_branch_charters_are_excluded():
    panel = _panel(_quarters(9, [100_000] * 6))
    panel["BKCLASS"] = "NC"
    labelled, _ = build_labels(panel, _inst([{"CERT": 9, "BKCLASS": "NC"}]))
    assert labelled.empty


def test_terminal_winddown_is_not_a_drawdown():
    """A bank reporting near-zero deposits at T+1 is surrendering its charter."""
    panel = _panel(_quarters(11, [100_000] * 5 + [0]))
    labelled, _ = build_labels(panel, _inst([{"CERT": 11}]))
    assert "2017Q1" not in set(labelled["quarter"])


def test_bank_that_disappears_is_dropped_not_labelled_zero():
    panel = _panel(_quarters(13, [100_000] * 6))
    labelled, _ = build_labels(panel, _inst([{"CERT": 13}]))
    # The final quarter has no T+1 row, so its label is undefined.
    assert "2017Q2" not in set(labelled["quarter"])


def test_acquirer_row_is_excluded_in_the_quarter_it_absorbs():
    """Absorbing another bank inflates T+1 deposits; that jump is not organic."""
    panel = _panel(_quarters(21, [100_000] * 5 + [190_000]))
    inst = _inst(
        [
            {"CERT": 21},
            {"CERT": 22, "ACTIVE": 0, "ENDEFYMD": "05/15/2017", "NEWCERT": 21, "RSSDHCR": "A"},
        ]
    )
    labelled, extra = build_labels(panel, inst)
    assert "2017Q1" not in set(labelled["quarter"])
    audit = extra["audit"].set_index("rule")["rows_removed"]
    assert audit["absorbed_another_bank_in_T+1 (inorganic jump)"] == 1


def test_de_novo_bank_is_excluded():
    panel = _panel(_quarters(31, [100_000] * 6))
    labelled, _ = build_labels(panel, _inst([{"CERT": 31, "ESTYMD": "01/01/2016"}]))
    assert labelled.empty


def test_audit_totals_reconcile_with_the_raw_panel():
    panel = _panel(_quarters(41, [100_000] * 6) + _quarters(42, [50_000] * 6))
    _, extra = build_labels(panel, _inst([{"CERT": 41}, {"CERT": 42}]))
    assert extra["audit"]["rows_removed"].sum() == len(panel)
