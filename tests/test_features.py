"""Feature and graduation-rule tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from drawdown_radar.features import add_lags, macro_deposit_growth, prepare
from drawdown_radar.registry import Status
from drawdown_radar.scorecards import _verdict


# --------------------------------------------------------------- lags
def test_lags_are_gap_safe_and_never_bridge_a_missing_quarter():
    """The trap groupby().shift() falls into: with 2016Q3 absent, lag1 at 2016Q4 must be NaN."""
    df = pd.DataFrame(
        {
            "cert": [1, 1, 1],
            "qidx": [8064, 8065, 8067],  # 2016Q1, 2016Q2, 2016Q4 -- 2016Q3 missing
            "x": [10.0, 20.0, 40.0],
        }
    )
    out = add_lags(df, ["x"], lags=(1,)).sort_values("qidx")
    assert pd.isna(out.loc[out.qidx == 8064, "x_lag1"].iloc[0])
    assert out.loc[out.qidx == 8065, "x_lag1"].iloc[0] == 10.0
    # 2016Q4's lag1 would be 2016Q3, which does not exist.
    assert pd.isna(out.loc[out.qidx == 8067, "x_lag1"].iloc[0])


def test_lags_do_not_leak_across_banks():
    df = pd.DataFrame({"cert": [1, 2], "qidx": [8065, 8065], "x": [1.0, 2.0]})
    out = add_lags(df, ["x"], lags=(1,))
    assert out["x_lag1"].isna().all()


# --------------------------------------------------------------- ratios
def _panel(rows):
    base = {
        "NAME": "T",
        "BKCLASS": "NM",
        "STALP": "CA",
        "ACTEVT": None,
        "DEPDOM": 1,
        "LIAB": 1,
        "SC": 1,
        "NIMY": 3.0,
        "ROA": 1,
        "ROE": 1,
        "LNLSDEPR": 1,
        "EQV": 1,
        "BROR": 1,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def test_unrealized_afs_loss_is_fair_value_minus_amortized_cost_over_equity():
    """SCAF - SCAA is the SVB mechanism; a negative value must read as a loss."""
    p = _panel(
        [
            {
                "CERT": 1,
                "quarter": "2020Q1",
                "REPDTE": "20200331",
                "DEP": 1000,
                "ASSET": 2000,
                "EQ": 100,
                "SCAF": 90,
                "SCAA": 100,  # $10 unrealized loss on $100 equity
                "LNLSNET": 500,
                "BRO": 0,
                "NTRTIME": 0,
                "COREDEP": 0,
                "DEPUNINS": 0,
                "DEPNI": 0,
            }
        ]
    )
    out = prepare(p)
    assert out["afs_unreal_to_eq"].iloc[0] == pytest.approx(-0.10)


def test_ratios_with_zero_denominator_are_missing_not_infinite():
    p = _panel(
        [
            {
                "CERT": 1,
                "quarter": "2020Q1",
                "REPDTE": "20200331",
                "DEP": 0,
                "ASSET": 0,
                "EQ": 0,
                "SCAF": 1,
                "SCAA": 1,
                "LNLSNET": 5,
                "BRO": 1,
                "NTRTIME": 1,
                "COREDEP": 1,
                "DEPUNINS": 1,
                "DEPNI": 1,
            }
        ]
    )
    out = prepare(p)
    for col in ["brokered_share", "ltd", "eq_assets", "afs_unreal_to_eq"]:
        assert pd.isna(out[col].iloc[0]), col
    assert not np.isinf(out[["brokered_share", "ltd", "eq_assets"]].to_numpy(dtype=float)).any()


def test_macro_deposit_growth_is_constant_within_a_quarter():
    """It is a regime covariate, so it must be identical for every bank in a quarter."""
    rows = []
    for cert, d0, d1 in [(1, 100.0, 110.0), (2, 200.0, 210.0)]:
        rows.append({"cert": cert, "qidx": 8064, "dep": d0})
        rows.append({"cert": cert, "qidx": 8065, "dep": d1})
    g = macro_deposit_growth(pd.DataFrame(rows))
    assert g.loc[8065] == pytest.approx((110 + 210) / (100 + 200) - 1)
    assert 8064 not in g.index  # no prior quarter to compare against


# --------------------------------------------------------------- graduation rules
def _row(**kw):
    base = {
        "incremental_lift_5pct": 0.0,
        "standalone_lift_5pct": 1.0,
        "standalone_lift_frac_folds_above_1": 0.5,
        "macro": False,
    }
    return pd.Series({**base, **kw})


def test_graduates_only_with_both_incremental_lift_and_stability():
    assert (
        _verdict(_row(incremental_lift_5pct=0.20, standalone_lift_frac_folds_above_1=1.0))[0]
        is Status.GRADUATED
    )
    # Useful but unstable -> PARKED, not GRADUATED.
    assert (
        _verdict(_row(incremental_lift_5pct=0.20, standalone_lift_frac_folds_above_1=0.4))[0]
        is Status.PARKED
    )


def test_informative_but_redundant_is_parked():
    v, reason = _verdict(_row(standalone_lift_5pct=2.5, incremental_lift_5pct=0.0))
    assert v is Status.PARKED
    assert "redundant" in reason


def test_content_free_signal_is_killed():
    assert _verdict(_row(standalone_lift_5pct=1.0, incremental_lift_5pct=0.0))[0] is Status.KILLED


def test_macro_signal_is_always_killed_regardless_of_pooled_appeal():
    v, reason = _verdict(_row(macro=True, standalone_lift_5pct=9.0, incremental_lift_5pct=9.0))
    assert v is Status.KILLED
    assert "within a quarter" in reason


def test_missing_evidence_is_treated_as_zero_not_as_a_pass():
    v, _ = _verdict(_row(incremental_lift_5pct=np.nan, standalone_lift_5pct=np.nan))
    assert v is Status.KILLED
