"""Balance-sheet stress and growth-dynamics candidates."""

from __future__ import annotations

import pandas as pd

from ..registry import register

GROWTH = "growth-dynamics"
STRESS = "balance-sheet-stress"


@register(
    "dep_growth_1q",
    "Current deposit momentum: the level of the trend a drawdown would have to break.",
    GROWTH,
)
def dep_growth_1q(df: pd.DataFrame) -> pd.Series:
    return df["g1"]


@register(
    "dep_growth_decel_4q",
    "Deceleration turns negative before the level breaks, so it should lead the event.",
    GROWTH,
)
def dep_growth_decel_4q(df: pd.DataFrame) -> pd.Series:
    trailing = df[["g2", "g3", "g4"]].mean(axis=1)
    return df["g1"] - trailing


@register(
    "dep_growth_vol_4q",
    "A volatile deposit book is a concentrated one; volatility itself is fragility.",
    GROWTH,
)
def dep_growth_vol_4q(df: pd.DataFrame) -> pd.Series:
    return df[["g1", "g2", "g3", "g4"]].std(axis=1)


@register(
    "asset_dep_divergence",
    "Assets outgrowing deposits means the gap is being plugged with borrowings.",
    GROWTH,
)
def asset_dep_divergence(df: pd.DataFrame) -> pd.Series:
    return df["ag1"] - df["g1"]


@register(
    "ltd_level",
    "A high loan-to-deposit ratio leaves no liquid cushion to absorb an outflow.",
    STRESS,
)
def ltd_level(df: pd.DataFrame) -> pd.Series:
    return df["ltd"]


@register(
    "ltd_trend_4q",
    "A rising loan-to-deposit ratio means loan growth is outrunning the funding base.",
    STRESS,
)
def ltd_trend_4q(df: pd.DataFrame) -> pd.Series:
    return df["ltd"] - df["ltd_lag4"]


@register(
    "eq_assets",
    "Capital cushion: thin equity invites depositor and supervisory scrutiny.",
    STRESS,
)
def eq_assets(df: pd.DataFrame) -> pd.Series:
    return df["eq_assets"]


@register(
    "eq_assets_chg_4q",
    "Eroding capital is the visible half of a balance sheet losing its buffer.",
    STRESS,
)
def eq_assets_chg_4q(df: pd.DataFrame) -> pd.Series:
    return df["eq_assets"] - df["eq_assets_lag4"]


@register(
    "unrealized_afs_loss_to_eq",
    "AFS fair value minus amortized cost over equity: paper losses that become real if "
    "deposits force a sale. This is the SVB mechanism.",
    STRESS,
)
def unrealized_afs_loss_to_eq(df: pd.DataFrame) -> pd.Series:
    return df["afs_unreal_to_eq"]


@register(
    "unrealized_afs_loss_chg_4q",
    "Deterioration in the securities mark is what turns a liquidity need into a solvency one.",
    STRESS,
)
def unrealized_afs_loss_chg_4q(df: pd.DataFrame) -> pd.Series:
    return df["afs_unreal_to_eq"] - df["afs_unreal_to_eq_lag4"]


@register(
    "nim_level",
    "Net interest margin is the price the bank is winning or losing deposits at.",
    STRESS,
)
def nim_level(df: pd.DataFrame) -> pd.Series:
    return df["nimy"]


@register(
    "nim_compression_4q",
    "Margin compression means the bank is paying up to keep funding, or already losing it.",
    STRESS,
)
def nim_compression_4q(df: pd.DataFrame) -> pd.Series:
    return df["nimy"] - df["nimy_lag4"]
