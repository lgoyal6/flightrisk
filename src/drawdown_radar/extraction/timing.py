"""Timing-leakage guard for the text signals, in the same spirit as the as-of audit.

A structured feature at quarter T comes from a Call Report *about* T. A text feature is
different: the 8-K that discusses Q1 is *filed* in April, i.e. inside Q2. So the rule cannot be
"filed in T" -- it has to be stated in terms of what was knowable at the decision point.

**The rule.** A document may inform the prediction for event quarter T+1 only if it was filed
strictly after quarter T ended and on or before the day T+1 opened... which is impossible, since
T+1 opens the day after T ends. So the usable window is: filed within quarter T+1 itself, up to
the point the alert list is produced. This project produces the alert list from information
available at the *start* of the prediction quarter, so:

    filed_quarter == T+1  AND  filing_date is in the first month of T+1

That keeps an April earnings release (discussing Q1, filed early Q2) eligible for predicting the
Q2 event, while excluding a late-June filing that would already contain most of the outcome.
Assigning that document to feature-row T is what makes it comparable to the structured signals.

Anything outside the window is dropped, counted, and reported -- never silently included.
"""

from __future__ import annotations

import pandas as pd

from ..config import next_quarter, quarter_index

# Documents must land in the first N months of the prediction quarter.
MAX_MONTH_OFFSET = 1


class TimingViolation(AssertionError):
    """Raised when a document would inform a quarter it could not have been known for."""


def quarter_of(date: pd.Timestamp) -> str:
    return f"{date.year}Q{date.quarter}"


def month_offset_in_quarter(date: pd.Timestamp) -> int:
    """0 for the first month of the quarter, 1 for the second, 2 for the third."""
    return (date.month - 1) % 3


def assign_feature_quarter(extractions: pd.DataFrame) -> pd.DataFrame:
    """Map each filing to the feature row (quarter T) whose prediction it may inform.

    A document filed early in quarter Q informs the prediction of the event in Q, which is
    carried on the feature row at T = Q - 1.
    """
    df = extractions.copy()
    df["filing_ts"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df = df[df["filing_ts"].notna()].copy()
    df["filed_quarter"] = df["filing_ts"].map(quarter_of)
    df["month_offset"] = df["filing_ts"].map(month_offset_in_quarter)

    # event quarter == the quarter it was filed in; feature row is the quarter before that
    df["event_quarter"] = df["filed_quarter"]
    df["qidx"] = df["filed_quarter"].map(quarter_index) - 1
    df["eligible"] = df["month_offset"] <= MAX_MONTH_OFFSET
    return df


def audit(df: pd.DataFrame, raise_on_violation: bool = True) -> pd.DataFrame:
    """Assert every retained document predates the quarter it informs. Wired into the pipeline."""
    if df.empty:
        return pd.DataFrame([{"check": "no documents", "violations": 0}])
    kept = df[df["eligible"]]
    rows = []

    # 1. The filing must fall inside the event quarter it is assigned to.
    bad_q = kept[kept["filed_quarter"] != kept["event_quarter"]]
    rows.append({"check": "filing_quarter == event_quarter", "violations": len(bad_q)})

    # 2. The feature row must be exactly one quarter before the event.
    off = kept["event_quarter"].map(quarter_index) - kept["qidx"]
    rows.append({"check": "feature row is event_quarter - 1", "violations": int((off != 1).sum())})

    # 3. No document may be filed after the first month of its event quarter.
    rows.append(
        {
            "check": f"filed within first {MAX_MONTH_OFFSET + 1} month(s) of event quarter",
            "violations": int((kept["month_offset"] > MAX_MONTH_OFFSET).sum()),
        }
    )

    # 4. The feature row must never be the same quarter the document was filed in.
    same = kept["qidx"] == kept["filed_quarter"].map(quarter_index)
    rows.append({"check": "feature quarter != filed quarter", "violations": int(same.sum())})

    report = pd.DataFrame(rows)
    report["rows_checked"] = len(kept)
    report["rows_dropped_ineligible"] = int((~df["eligible"]).sum())
    total = int(report["violations"].sum())
    if total and raise_on_violation:
        detail = report[report["violations"] > 0].to_string(index=False)
        raise TimingViolation(f"text signals would use future information:\n{detail}")
    return report


def to_feature_rows(extractions: pd.DataFrame) -> pd.DataFrame:
    """Eligible documents collapsed to one row per (cert, feature quarter T)."""
    df = assign_feature_quarter(extractions)
    audit(df)
    kept = df[df["eligible"] & df.get("error").isna()] if "error" in df else df[df["eligible"]]
    if kept.empty:
        return pd.DataFrame()
    agg = (
        kept.sort_values("filing_ts")
        .groupby(["cert", "qidx"], as_index=False)
        .agg(
            txt_deposit_pressure=("deposit_pressure_mentioned", "max"),
            txt_funding_tone=("funding_concern_tone", "max"),
            txt_outflow_language=("explicit_outflow_language", "max"),
            txt_inflow_language=("explicit_inflow_language", "max"),
            n_docs=("accession", "size"),
            last_filing_date=("filing_date", "last"),
        )
    )
    for c in ["txt_deposit_pressure", "txt_outflow_language", "txt_inflow_language"]:
        agg[c] = agg[c].astype(float)
    agg["txt_funding_tone"] = agg["txt_funding_tone"].astype(float)
    return agg


def next_event_quarter(t: str) -> str:
    return next_quarter(t)
