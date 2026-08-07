"""Funding-mix candidates: who is paying for the deposits, and how loyal that money is."""

from __future__ import annotations

import pandas as pd

from ..config import DRAWDOWN_THRESHOLD
from ..registry import register

FAM = "funding-mix"


@register(
    "brokered_share",
    "Brokered deposits are intermediated hot money that leaves on price, not relationship.",
    FAM,
)
def brokered_share(df: pd.DataFrame) -> pd.Series:
    return df["brokered_share"]


@register(
    "brokered_share_chg_4q",
    "A bank replacing lost core funding with brokered money is already under strain.",
    FAM,
)
def brokered_share_chg_4q(df: pd.DataFrame) -> pd.Series:
    return df["brokered_share"] - df["brokered_share_lag4"]


@register(
    "time_dep_share",
    "Time deposits are rate-sensitive money that can walk at each maturity date.",
    FAM,
)
def time_dep_share(df: pd.DataFrame) -> pd.Series:
    return df["time_share"]


@register(
    "time_dep_share_chg_4q",
    "A shift into time deposits means depositors are repricing the relationship.",
    FAM,
)
def time_dep_share_chg_4q(df: pd.DataFrame) -> pd.Series:
    return df["time_share"] - df["time_share_lag4"]


@register(
    "core_dep_share",
    "FDIC's own split of relationship funding from purchased funding.",
    FAM,
)
def core_dep_share(df: pd.DataFrame) -> pd.Series:
    return df["core_share"]


@register(
    "core_dep_share_chg_4q",
    "Falling core share means the franchise is being replaced by wholesale money.",
    FAM,
)
def core_dep_share_chg_4q(df: pd.DataFrame) -> pd.Series:
    return df["core_share"] - df["core_share_lag4"]


@register(
    "uninsured_dep_share",
    "Uninsured balances are the flight-risk tranche; the depositor has a reason to run.",
    FAM,
)
def uninsured_dep_share(df: pd.DataFrame) -> pd.Series:
    return df["uninsured_share"]


@register(
    "uninsured_dep_share_chg_4q",
    "A rising uninsured share concentrates run risk even at constant total deposits.",
    FAM,
)
def uninsured_dep_share_chg_4q(df: pd.DataFrame) -> pd.Series:
    return df["uninsured_share"] - df["uninsured_share_lag4"]


@register(
    "noninterest_dep_share",
    "Noninterest-bearing balances are operational and stickiest; a high share is a moat.",
    FAM,
)
def noninterest_dep_share(df: pd.DataFrame) -> pd.Series:
    return df["noninterest_share"]


@register(
    "noninterest_dep_share_chg_4q",
    "Operational balances draining is the earliest tell that a treasury relationship is going.",
    FAM,
)
def noninterest_dep_share_chg_4q(df: pd.DataFrame) -> pd.Series:
    return df["noninterest_share"] - df["noninterest_share_lag4"]


@register(
    "prior_drawdown_1q",
    "Persistence: outflows arrive in runs, so last quarter's drawdown predicts the next.",
    "history",
)
def prior_drawdown_1q(df: pd.DataFrame) -> pd.Series:
    return (df["g1"] <= DRAWDOWN_THRESHOLD).astype(float)


@register(
    "prior_drawdown_count_4q",
    "Repeated drawdowns over a year separate a chronic bleed from one lumpy quarter.",
    "history",
)
def prior_drawdown_count_4q(df: pd.DataFrame) -> pd.Series:
    cols = ["g1", "g2", "g3", "g4"]
    return (df[cols] <= DRAWDOWN_THRESHOLD).sum(axis=1).astype(float)
