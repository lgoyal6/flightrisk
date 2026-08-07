"""Controls and deliberately-null tripwires.

The controls are registered rather than quietly added to the design matrix, so that "size did
the work" is a hypothesis the framework tests instead of a criticism it invites.

The dumb signals are semantically null by construction. If any of them GRADUATES, the
graduation framework is broken and the whole result set is void -- they are a live tripwire on
the pipeline, not filler. `registry.check_integrity()` raises if one ever reaches GRADUATED.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..registry import register

CTL = "control"
DUMB = "null-tripwire"


@register(
    "log_assets",
    "Bank size. Expected confounder: small banks have lumpier books, so size may carry "
    "apparent signal that is really heteroskedasticity.",
    CTL,
    is_control=True,
)
def log_assets(df: pd.DataFrame) -> pd.Series:
    return df["log_assets"]


@register(
    "quarter_of_year",
    "Calendar seasonality: Q2 events peak at 6.79% against ~5.2% elsewhere, consistent with "
    "April tax payments draining balances.",
    CTL,
    is_control=True,
    is_macro=True,
)
def quarter_of_year(df: pd.DataFrame) -> pd.Series:
    return df["quarter_of_year"].astype(float)


@register(
    "macro_agg_dep_growth",
    "Aggregate system-wide deposit growth this quarter -- the macro regime itself. Constant "
    "within a quarter, so it can only inflate pooled metrics, never rank a single quarter.",
    CTL,
    is_control=True,
    is_macro=True,
)
def macro_agg_dep_growth(df: pd.DataFrame) -> pd.Series:
    return df["macro_agg_dep_growth"]


# --------------------------------------------------------------------- tripwires
@register(
    "dumb_cert_parity",
    "Deliberately null: whether the FDIC certificate number is even. No economic content.",
    DUMB,
    is_dumb=True,
)
def dumb_cert_parity(df: pd.DataFrame) -> pd.Series:
    return (df["cert"] % 2).astype(float)


@register(
    "state_identity",
    "Home-state identifier. Registered as a CONTROL, not a feature: state drawdown rates run "
    "from 1.68% (ME) to 15.13% (NV), a 9x spread, so geography is real but it is a confounder "
    "rather than a leading indicator of any one customer's behaviour.",
    CTL,
    is_control=True,
    in_model=False,
)
def state_identity(df: pd.DataFrame) -> pd.Series:
    """Originally registered as `dumb_state_alpha_rank` -- a tripwire that fired correctly.

    The intent was that "alphabetical rank of the state" is meaningless. It is not: `.cat.codes`
    produces a state IDENTIFIER, and a tree splits it into arbitrary subsets of states without
    caring that the ordering is alphabetical. It scored standalone lift 1.75 and beat random
    ranking in 100% of folds -- because it encodes regional deposit dynamics.

    The general lesson, which is why `dumb_row_noise` replaced it: in a panel, ANY stable
    entity identifier is non-null, since it lets a model memorise entity-level base rates. A
    genuinely null feature has to be random per ROW, not per entity.
    """
    return df["STALP"].astype("category").cat.codes.astype(float)


@register(
    "dumb_row_noise",
    "Deliberately null: seeded pseudorandom value per bank-quarter. Independent of the entity, "
    "so unlike a stable identifier it cannot encode entity-level base rates.",
    DUMB,
    is_dumb=True,
)
def dumb_row_noise(df: pd.DataFrame) -> pd.Series:
    # Deterministic in (cert, qidx) so the pipeline stays reproducible across runs.
    mixed = df["cert"].astype("int64") * 2_654_435_761 + df["qidx"].astype("int64") * 40_503
    return ((mixed % 1_000_003) / 1_000_003.0).astype(float)


@register(
    "dumb_asset_digit_sum",
    "Deliberately null: digit sum of total assets. Scale-free numerology, no content.",
    DUMB,
    is_dumb=True,
)
def dumb_asset_digit_sum(df: pd.DataFrame) -> pd.Series:
    a = df["asset"].fillna(0).abs().astype("int64").astype(str)
    return a.map(lambda s: float(sum(int(c) for c in s))).replace(0, np.nan)
