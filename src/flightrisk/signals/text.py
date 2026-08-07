"""Unstructured-modality candidates: LLM-extracted flags from SEC 8-K Item 2.02 press releases.

These enter the same registry, the same as-of audit, and the same graduation protocol as the
balance-sheet signals -- no special treatment. They are `in_model=False` because coverage is 50
banks of ~4,350: putting them in the default model would restrict it to the covered subset. They
are evaluated on their own terms in `pipeline.run_text()`, which measures **incremental** lift
over the graduated structured set *within the >$10B stratum*, the only fair comparison given
coverage.

Missing text is not a zero. A bank-quarter with no filing gets NaN, so HistGB routes it as
missing rather than treating silence as "management said nothing concerning".
"""

from __future__ import annotations

import pandas as pd

from ..registry import register

FAM = "unstructured-text"


@register(
    "txt_deposit_pressure",
    "Management explicitly discusses difficulty or competition in gathering and retaining "
    "deposits -- intent and pressure that a balance-sheet ratio only shows after the fact.",
    FAM,
    in_model=False,
)
def txt_deposit_pressure(df: pd.DataFrame) -> pd.Series:
    return df.get("txt_deposit_pressure", pd.Series(index=df.index, dtype=float))


@register(
    "txt_funding_tone",
    "Ordinal 0-3 read on how concerned management sounds about funding; tone can move before "
    "any reported balance does.",
    FAM,
    in_model=False,
)
def txt_funding_tone(df: pd.DataFrame) -> pd.Series:
    return df.get("txt_funding_tone", pd.Series(index=df.index, dtype=float))


@register(
    "txt_outflow_language",
    "Management states deposits actually declined -- a directional claim, distinct from merely "
    "noting competition for deposits.",
    FAM,
    in_model=False,
)
def txt_outflow_language(df: pd.DataFrame) -> pd.Series:
    return df.get("txt_outflow_language", pd.Series(index=df.index, dtype=float))


@register(
    "txt_inflow_language",
    "Management states deposits grew. Registered as a candidate rather than assumed protective: "
    "per the U-shape, lumpy inflows are themselves a drawdown risk.",
    FAM,
    in_model=False,
)
def txt_inflow_language(df: pd.DataFrame) -> pd.Series:
    return df.get("txt_inflow_language", pd.Series(index=df.index, dtype=float))
