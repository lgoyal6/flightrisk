"""Feature preparation. Every column here is computable from information dated <= T.

Lags are built by an explicit merge on `qidx - k`, not `groupby().shift(k)`. With a gap in a
bank's reporting history, `shift(1)` returns the previous *observation* rather than the
previous *quarter*, which would silently turn a two-quarter change into a one-quarter change.
This is the same trap the label construction avoids.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Raw FDIC columns coerced to numeric, lowercased for use in signals.
NUMERIC = {
    "dep": "DEP",
    "asset": "ASSET",
    "eq": "EQ",
    "lnlsnet": "LNLSNET",
    "bro": "BRO",
    "ntrtime": "NTRTIME",
    "coredep": "COREDEP",
    "depunins": "DEPUNINS",
    "depni": "DEPNI",
    "scaf": "SCAF",
    "scaa": "SCAA",
    "sc": "SC",
    "nimy": "NIMY",
}

# Columns that get trailing lags. Shares and ratios are lagged so a signal can express
# "change in funding mix over the past year" without touching the future.
LAG_COLS = [
    "dep",
    "asset",
    "brokered_share",
    "time_share",
    "core_share",
    "uninsured_share",
    "noninterest_share",
    "ltd",
    "eq_assets",
    "nimy",
    "afs_unreal_to_eq",
]
LAGS = (1, 2, 3, 4, 5)


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    """Ratio with a zero/negative denominator treated as missing rather than infinite."""
    out = a / b.where(b > 0)
    return out.replace([np.inf, -np.inf], np.nan)


def add_lags(df: pd.DataFrame, cols: list[str], lags=LAGS) -> pd.DataFrame:
    """Gap-safe lags: join each column from the row exactly k quarters earlier."""
    out = df
    base = df[["cert", "qidx", *cols]]
    for k in lags:
        shifted = base.copy()
        shifted["qidx"] = shifted["qidx"] + k  # so it lands on the row k quarters later
        shifted = shifted.rename(columns={c: f"{c}_lag{k}" for c in cols})
        out = out.merge(shifted, on=["cert", "qidx"], how="left")
    return out


def macro_deposit_growth(panel: pd.DataFrame) -> pd.Series:
    """Aggregate QoQ deposit growth per quarter, on a constant panel of banks.

    Restricted to banks present in both quarters, so this measures flows rather than the
    composition change from consolidation. Observable as of T, and CONSTANT within a quarter --
    which is exactly why it has no within-quarter ranking power.
    """
    d = panel[["cert", "qidx", "dep"]].dropna()
    prev = d.copy()
    prev["qidx"] = prev["qidx"] + 1
    both = d.merge(prev, on=["cert", "qidx"], how="inner", suffixes=("", "_prev"))
    agg = both.groupby("qidx").agg(now=("dep", "sum"), before=("dep_prev", "sum"))
    return (agg["now"] / agg["before"] - 1).rename("macro_agg_dep_growth")


def prepare(panel: pd.DataFrame) -> pd.DataFrame:
    """Attach numeric columns, as-of ratios, trailing lags, and the macro covariate."""
    df = panel.copy()
    df["cert"] = df["CERT"].astype("int64")
    if "qidx" not in df.columns:
        df["qidx"] = df["quarter"].map(lambda q: int(q[:4]) * 4 + int(q[-1]) - 1)
    for lo, up in NUMERIC.items():
        df[lo] = pd.to_numeric(df[up], errors="coerce")

    # ---- as-of-T ratios ----
    df["brokered_share"] = _safe_div(df["bro"], df["dep"])
    df["time_share"] = _safe_div(df["ntrtime"], df["dep"])
    df["core_share"] = _safe_div(df["coredep"], df["dep"])
    df["uninsured_share"] = _safe_div(df["depunins"], df["dep"])
    df["noninterest_share"] = _safe_div(df["depni"], df["dep"])
    df["ltd"] = _safe_div(df["lnlsnet"], df["dep"])
    df["eq_assets"] = _safe_div(df["eq"], df["asset"])
    # (fair value - amortized cost) / equity. Negative = unrealized loss eating the cushion.
    df["afs_unreal_to_eq"] = _safe_div(df["scaf"] - df["scaa"], df["eq"])
    df["log_assets"] = np.log(df["asset"].where(df["asset"] > 0))

    df = df.sort_values(["cert", "qidx"]).reset_index(drop=True)
    df = add_lags(df, LAG_COLS)

    # ---- trailing one-quarter growth rates, each ending k quarters back ----
    for k in (0, 1, 2, 3):
        num = df["dep_lag" + str(k)] if k else df["dep"]
        den = df[f"dep_lag{k + 1}"]
        df[f"g{k + 1}"] = _safe_div(num, den) - 1
    df["ag1"] = _safe_div(df["asset"], df["asset_lag1"]) - 1

    df["quarter_of_year"] = df["qidx"] % 4 + 1
    df = df.merge(macro_deposit_growth(df).reset_index(), on="qidx", how="left")
    return df
