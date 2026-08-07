"""Measure the extractor before trusting anything built on it.

A broken extractor's downstream lift is meaningless -- if the flags are noise, "text adds no
incremental lift" is a statement about the extractor, not about text. So accuracy is measured
against a hand-labelled sample first, and reported per flag, before the signals are registered.

The labels live in `data/manual/extraction_validation.csv` (version-controlled, one row per
document, `label_*` columns). `build_worksheet()` emits the excerpts to read; labels are filled
in by hand and committed. Precision/recall are reported per flag, plus agreement on the ordinal
tone field, so a flag that is individually unreliable can be dropped rather than dragging the set.
"""

from __future__ import annotations

import pandas as pd

from ..config import DATA, RAW, REPORTS

LABELS = DATA / "manual" / "extraction_validation.csv"
WORKSHEET = REPORTS / "extraction_validation_worksheet.csv"

BOOL_FLAGS = [
    "deposit_pressure_mentioned",
    "explicit_outflow_language",
    "explicit_inflow_language",
]
TONE = "funding_concern_tone"


def build_worksheet(extractions: pd.DataFrame, n: int = 30, seed: int = 7) -> pd.DataFrame:
    """Sample documents for hand labelling, stratified so the sample is not all one class.

    A random 30 out of ~700 bank-quarters would be dominated by the majority class and would
    measure precision on almost no positives. Sampling across the extractor's own predicted
    classes gives every flag some positives and some negatives to be right or wrong about.
    """
    df = extractions[extractions.get("error").isna()] if "error" in extractions else extractions
    df = df.copy()
    df["stratum"] = (
        df["deposit_pressure_mentioned"].astype(int).astype(str)
        + df["explicit_outflow_language"].astype(int).astype(str)
        + df[TONE].clip(0, 3).astype(str)
    )
    per = max(1, n // max(df["stratum"].nunique(), 1))
    parts = [
        g.sample(min(len(g), per), random_state=seed) for _, g in df.groupby("stratum", sort=True)
    ]
    sample = pd.concat(parts).head(n) if parts else df.head(0)
    if len(sample) < n:  # top up if some strata were thin
        extra = df[~df["accession"].isin(sample["accession"])].sample(
            min(n - len(sample), len(df) - len(sample)), random_state=seed
        )
        sample = pd.concat([sample, extra])
    # The worksheet deliberately shows the DOCUMENT and withholds the extractor's own answer.
    # Including `evidence_quote` or the predicted `stratum` would anchor the labeller on the
    # prediction being graded, and the resulting "agreement" would measure nothing. Stratum is
    # used to *select* the sample and then dropped.
    cols = ["cert", "bank", "filing_date", "accession", "cik"]
    out = sample[[c for c in cols if c in sample.columns]].copy()
    out["accession"] = _norm_accession(out["accession"])
    out["document_passages"] = [_passages(r) for _, r in sample.iterrows()]
    for f in BOOL_FLAGS:
        out[f"label_{f}"] = ""
    out[f"label_{TONE}"] = ""
    out.to_csv(WORKSHEET, index=False)
    return out


def _passages(row: pd.Series, width: int = 320, limit: int = 6) -> str:
    """Deposit/funding passages from the raw document, for blind hand labelling."""
    import re

    from ..config import RAW
    from .extract import html_to_text

    path = RAW / "edgar" / "docs" / f"{int(row['cik'])}_{row['accession']}.html"
    if not path.exists():
        return ""
    text = html_to_text(path.read_text())
    pat = re.compile(r"(deposit|funding|liquidity|outflow|inflow)", re.IGNORECASE)
    seen, out = set(), []
    for m in pat.finditer(text):
        s = max(0, m.start() - width // 2)
        chunk = text[s : s + width]
        key = chunk[:60]
        if key in seen:
            continue
        seen.add(key)
        # Skip passages that are mostly digits -- those are table rows, not commentary.
        if sum(c.isdigit() for c in chunk) / max(len(chunk), 1) > 0.25:
            continue
        out.append(chunk.strip())
        if len(out) >= limit:
            break
    return " ||| ".join(out)


def _norm_accession(s: pd.Series) -> pd.Series:
    """18-digit zero-padded accession, whichever way the value survived serialisation."""
    return s.astype(str).str.replace(r"\D", "", regex=True).str.zfill(18)


def _as_bool(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.lower()
        .map(
            {
                "true": True,
                "1": True,
                "yes": True,
                "y": True,
                "false": False,
                "0": False,
                "no": False,
                "n": False,
            }
        )
    )


def score(extractions: pd.DataFrame) -> pd.DataFrame:
    """Per-flag precision / recall / accuracy against the hand labels."""
    if not LABELS.exists():
        raise FileNotFoundError(f"missing hand labels at {LABELS}")
    lab = pd.read_csv(LABELS)
    pred = extractions.copy()
    # EDGAR accession numbers are 18-digit zero-padded strings. A CSV round-trip parses them as
    # int64 and silently eats the leading zeros ("000003696622000005" -> 3696622000005), so a
    # naive astype(str) merge matches nothing. Normalise both sides to 18-char zero-padded.
    lab["accession"] = _norm_accession(lab["accession"])
    pred["accession"] = _norm_accession(pred["accession"])
    m = lab.merge(pred, on="accession", how="inner", suffixes=("_lab", ""))
    if m.empty:
        raise ValueError("no hand-labelled documents matched the extractions")
    rows = []
    for f in BOOL_FLAGS:
        y = _as_bool(m[f"label_{f}"])
        p = m[f].astype(bool)
        ok = y.notna()
        y, p = y[ok].astype(bool), p[ok]
        tp = int((y & p).sum())
        fp = int((~y & p).sum())
        fn = int((y & ~p).sum())
        tn = int((~y & ~p).sum())
        rows.append(
            {
                "flag": f,
                "n": int(ok.sum()),
                "n_positive_labels": int(y.sum()),
                "precision": tp / max(tp + fp, 1),
                "recall": tp / max(tp + fn, 1),
                "accuracy": (tp + tn) / max(int(ok.sum()), 1),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
            }
        )
    yt = pd.to_numeric(m[f"label_{TONE}"], errors="coerce")
    pt = pd.to_numeric(m[TONE], errors="coerce")
    ok = yt.notna() & pt.notna()
    rows.append(
        {
            "flag": TONE,
            "n": int(ok.sum()),
            "n_positive_labels": int((yt[ok] > 0).sum()),
            "accuracy": float((yt[ok] == pt[ok]).mean()) if ok.any() else float("nan"),
            "within_1": float((abs(yt[ok] - pt[ok]) <= 1).mean()) if ok.any() else float("nan"),
            "mean_abs_error": float(abs(yt[ok] - pt[ok]).mean()) if ok.any() else float("nan"),
        }
    )
    out = pd.DataFrame(rows)
    out.to_csv(REPORTS / "extraction_accuracy.csv", index=False)
    return out


def load_extractions() -> pd.DataFrame:
    p = RAW / "llm_extractions.parquet"
    if not p.exists():
        raise FileNotFoundError("run the extraction step first")
    return pd.read_parquet(p)
