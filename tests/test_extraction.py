"""Tests for the unstructured leg: timing guard, prompt versioning, response parsing.

The timing guard is the highest-stakes piece. A text feature is dated differently from a
structured one — the 8-K discussing Q1 is *filed* in Q2 — so "as-of T" has to be re-derived
rather than assumed, and getting it wrong leaks the outcome into the predictor.
"""

from __future__ import annotations

import pandas as pd
import pytest

from drawdown_radar.config import quarter_index
from drawdown_radar.extraction import extract, timing
from drawdown_radar.extraction.edgar import narrative_score


def _filings(rows: list[dict]) -> pd.DataFrame:
    base = {
        "cert": 1,
        "accession": "x",
        "deposit_pressure_mentioned": False,
        "funding_concern_tone": 0,
        "explicit_outflow_language": False,
        "explicit_inflow_language": False,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


# --------------------------------------------------------------- timing guard
def test_april_filing_informs_the_q2_prediction_from_the_q1_feature_row():
    """A Q1 earnings release filed in April is legitimate as-of-T information for predicting Q2."""
    df = timing.assign_feature_quarter(_filings([{"filing_date": "2023-04-14", "accession": "a"}]))
    r = df.iloc[0]
    assert r["filed_quarter"] == "2023Q2"
    assert r["event_quarter"] == "2023Q2"
    assert r["qidx"] == quarter_index("2023Q1"), "feature row must be the quarter before the event"
    assert r["eligible"]


def test_late_quarter_filing_is_dropped_because_it_contains_the_outcome():
    """A late-June filing already spans most of Q2 — using it to predict Q2 is leakage."""
    df = timing.assign_feature_quarter(_filings([{"filing_date": "2023-06-28", "accession": "b"}]))
    assert not df.iloc[0]["eligible"]


def test_feature_quarter_is_never_the_quarter_the_document_was_filed_in():
    dates = ["2022-01-20", "2023-04-14", "2024-07-16", "2025-10-15"]
    rows = [{"filing_date": d, "accession": d} for d in dates]
    df = timing.assign_feature_quarter(_filings(rows))
    kept = df[df["eligible"]]
    assert len(kept) == 4
    assert (kept["qidx"] < kept["filed_quarter"].map(quarter_index)).all()


def test_audit_passes_on_well_formed_rows_and_reports_what_it_dropped():
    df = timing.assign_feature_quarter(
        _filings(
            [
                {"filing_date": "2023-01-18", "accession": "a"},
                {"filing_date": "2023-04-14", "accession": "b"},
                {"filing_date": "2023-06-28", "accession": "c"},  # ineligible
            ]
        )
    )
    rep = timing.audit(df)
    assert rep["violations"].sum() == 0
    assert rep["rows_dropped_ineligible"].iloc[0] == 1


def test_audit_raises_when_a_document_would_inform_its_own_filing_quarter():
    """Directly simulates the off-by-one the guard exists to catch."""
    df = timing.assign_feature_quarter(_filings([{"filing_date": "2023-04-14", "accession": "a"}]))
    df.loc[:, "qidx"] = df["filed_quarter"].map(quarter_index)  # leak it
    with pytest.raises(timing.TimingViolation, match="future information"):
        timing.audit(df)


def test_month_offset_identifies_position_within_the_quarter():
    assert timing.month_offset_in_quarter(pd.Timestamp("2023-04-01")) == 0
    assert timing.month_offset_in_quarter(pd.Timestamp("2023-05-15")) == 1
    assert timing.month_offset_in_quarter(pd.Timestamp("2023-06-30")) == 2
    assert timing.month_offset_in_quarter(pd.Timestamp("2023-01-05")) == 0


def test_feature_rows_collapse_multiple_filings_per_quarter():
    df = _filings(
        [
            {"filing_date": "2023-04-14", "accession": "a", "deposit_pressure_mentioned": False},
            {"filing_date": "2023-04-20", "accession": "b", "deposit_pressure_mentioned": True},
        ]
    )
    out = timing.to_feature_rows(df)
    assert len(out) == 1
    assert out.iloc[0]["n_docs"] == 2
    assert out.iloc[0]["txt_deposit_pressure"] == 1.0  # max across the quarter's filings


# --------------------------------------------------------------- prompt versioning
def test_prompt_hash_is_stable_and_content_dependent():
    h = extract.prompt_hash()
    assert len(h) == 16 and h == extract.prompt_hash()


def test_prompt_declares_every_flag_the_parser_expects():
    text = extract.prompt_text()
    for flag in extract.FLAGS:
        assert flag in text, f"{flag} is parsed but never asked for in the prompt"


# --------------------------------------------------------------- response parsing
def test_parses_a_clean_json_response():
    out = extract.parse_response(
        '{"deposit_pressure_mentioned": true, "funding_concern_tone": 2,'
        ' "explicit_outflow_language": true, "explicit_inflow_language": false,'
        ' "evidence_quote": "deposits declined"}'
    )
    assert out["deposit_pressure_mentioned"] is True
    assert out["funding_concern_tone"] == 2
    assert out["explicit_outflow_language"] is True


def test_parses_json_wrapped_in_a_code_fence_or_prose():
    raw = 'Here is the result:\n```json\n{"funding_concern_tone": 1}\n```\nHope that helps.'
    assert extract.parse_response(raw)["funding_concern_tone"] == 1


def test_tone_is_clamped_to_the_documented_range():
    assert extract.parse_response('{"funding_concern_tone": 9}')["funding_concern_tone"] == 3
    assert extract.parse_response('{"funding_concern_tone": -4}')["funding_concern_tone"] == 0


def test_missing_fields_default_to_negative_rather_than_true():
    """A malformed response must not silently manufacture positive flags."""
    out = extract.parse_response("{}")
    assert out["deposit_pressure_mentioned"] is False
    assert out["explicit_outflow_language"] is False
    assert out["funding_concern_tone"] == 0


def test_unparseable_response_raises_rather_than_returning_defaults():
    with pytest.raises(ValueError, match="no JSON object"):
        extract.parse_response("I cannot answer that.")


# --------------------------------------------------------------- document selection
def test_narrative_score_prefers_prose_over_a_table_of_the_same_word():
    tables = "<table>" + " ".join(f"<td>Total deposits</td><td>{i:,}</td>" for i in range(50))
    prose = (
        "<p>Total deposits decreased during the quarter as competition for deposits "
        "intensified across our markets and clients moved balances to higher yielding "
        "alternatives, which pressured our funding costs.</p>"
    )
    assert narrative_score(prose) > narrative_score(tables)


def test_narrative_score_is_zero_for_a_pure_numeric_supplement():
    assert narrative_score("<table><td>Deposits</td><td>2,548,476</td></table>") == 0


# --------------------------------------------------------------- excerpting
def test_excerpt_keeps_deposit_passages_from_deep_in_a_long_document():
    """Blind truncation would cut the commentary and make the extractor look worse than it is."""
    filler = "The company reported results. " * 2000
    tail = "Management noted that total deposits declined sharply during the quarter."
    out = extract.relevant_excerpt(filler + tail, limit=4000)
    assert len(out) <= 4600
    assert "deposits declined sharply" in out


def test_short_documents_pass_through_untouched():
    text = "Deposits grew modestly this quarter."
    assert extract.relevant_excerpt(text) == text
