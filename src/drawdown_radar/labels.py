"""Label construction: next-quarter deposit drawdown, with explicit merger/de-novo handling.

Two design choices matter for correctness:

1. **Adjacency is computed by explicit quarter index, never by `groupby().shift()`.**
   A bank with a missing quarter would have `shift(-1)` silently pair T with T+2 and
   present a two-quarter change as a one-quarter change. We join on `qidx + 1`.

2. **A bank that disappears is not a drawdown.** Absence of a T+1 row means the label is
   *undefined*, not zero. Same for a bank that absorbed another: its deposit jump is
   inorganic. Every exclusion is counted and reported rather than quietly applied.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import (
    DATA,
    DE_NOVO_QUARTERS,
    DRAWDOWN_THRESHOLD,
    EXCLUDED_BKCLASS,
    MIN_DEPOSITS_USD_K,
    MIN_HISTORY_QUARTERS,
    REPORTS,
    SEVERE_THRESHOLD,
    next_quarter,
    quarter_index,
)


def _clean_id(series: pd.Series) -> pd.Series:
    """Normalise an FDIC id column to a nullable string.

    `RSSDHCR` is a *string* column in which "no holding company" is encoded as an EMPTY
    STRING (6,756 institutions), not as null. Because `"" == ""` is True, comparing raw
    values makes every pair of independent, HC-less banks look like affiliates of the same
    parent -- which silently over-excluded ordinary community-bank mergers.
    """
    s = series.astype("string").str.strip()
    return s.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "0": pd.NA})


def _date_to_qidx(series: pd.Series) -> pd.Series:
    """FDIC dates are MM/DD/YYYY strings -> monotonic quarter index."""
    dt = pd.to_datetime(series, format="%m/%d/%Y", errors="coerce")
    return dt.dt.year * 4 + (dt.dt.quarter - 1)


def merger_events(inst: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Derive (exits, absorptions) from institution metadata.

    `NEWCERT` on an inactive institution points at its successor (97% populated), so it
    identifies both sides of a merger: the bank that vanished and the bank that grew.
    """
    inactive = inst[inst["ACTIVE"] == 0].copy()
    inactive["end_qidx"] = _date_to_qidx(inactive["ENDEFYMD"])
    inactive["NEWCERT"] = pd.to_numeric(inactive["NEWCERT"], errors="coerce")

    exits = inactive.loc[
        inactive["end_qidx"].notna(), ["CERT", "end_qidx", "NEWCERT", "RSSDHCR"]
    ].rename(columns={"CERT": "cert"})
    exits["was_acquired"] = exits["NEWCERT"].notna() & (exits["NEWCERT"] > 0)

    # Same-holding-company flag: was the successor an affiliate under the same parent?
    # This distinguishes an intra-HC *charter consolidation* (deposits shuffled to a sibling
    # bank -- not organic outflow) from a genuine third-party acquisition or a self-
    # liquidation like Silvergate. Without it, Wilmington Trust NA's -99.9% transfer into
    # M&T reads as the largest "drawdown" in the panel.
    hc = inst.drop_duplicates("CERT").copy()
    hc["cert_i"] = hc["CERT"].astype("int64")
    hc_map = _clean_id(hc.set_index("cert_i")["RSSDHCR"])
    succ_hc = exits["NEWCERT"].map(hc_map)
    own_hc = exits["cert"].astype("int64").map(hc_map)
    exits["same_hc_successor"] = (own_hc.notna() & succ_hc.notna() & (own_hc == succ_hc)).fillna(
        False
    )

    # An absorption is the successor's side of the same event.
    absorbed = exits[exits["was_acquired"]].copy()
    absorptions = (
        absorbed.groupby(["NEWCERT", "end_qidx"], as_index=False)
        .size()
        .rename(columns={"NEWCERT": "cert", "size": "n_absorbed"})
    )
    absorptions["cert"] = absorptions["cert"].astype("int64")
    return exits, absorptions


EXTREME_DECLINE = -0.50  # threshold for surfacing a row to manual review
MANUAL_EXCLUSIONS = DATA / "manual" / "label_exclusions.csv"


def _manual_exclusion_mask(df: pd.DataFrame) -> pd.Series:
    """Rows listed in the version-controlled adjudication file (cert, quarter, reason)."""
    if not MANUAL_EXCLUSIONS.exists():
        return pd.Series(False, index=df.index)
    man = pd.read_csv(MANUAL_EXCLUSIONS)
    keys = set(zip(man["cert"].astype("int64"), man["quarter"].astype(str), strict=False))
    return pd.Series(
        [(c, q) in keys for c, q in zip(df["cert"], df["quarter"], strict=False)],
        index=df.index,
    )


def write_review_file(df: pd.DataFrame) -> Path:
    """Surface every extreme decline for human adjudication. Reviewed, not auto-trusted."""
    cols = ["cert", "NAME", "quarter", "dep", "dep_next", "dep_growth_next", "BKCLASS", "STALP"]
    rev = df[df["dep_growth_next"] <= EXTREME_DECLINE].sort_values("dep_growth_next")
    out = REPORTS / "extreme_declines_review.csv"
    rev[cols].to_csv(out, index=False)
    return out


def build_labels(panel: pd.DataFrame, inst: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (labelled rows, exclusion audit table).

    One row per (CERT, quarter T) carrying the T+1 outcome. Rows failing any exclusion are
    removed and tallied; the caller reports the tally.
    """
    df = panel.copy()
    df["cert"] = df["CERT"].astype("int64")
    df["qidx"] = df["quarter"].map(quarter_index)
    df["dep"] = pd.to_numeric(df["DEP"], errors="coerce")
    df = df.sort_values(["cert", "qidx"]).reset_index(drop=True)

    # ---- next-quarter deposits by explicit adjacency join (never shift) ----
    nxt = df[["cert", "qidx", "dep"]].copy()
    nxt["qidx"] = nxt["qidx"] - 1  # so it aligns onto row T
    nxt = nxt.rename(columns={"dep": "dep_next"})
    df = df.merge(nxt, on=["cert", "qidx"], how="left")

    df["dep_growth_next"] = (df["dep_next"] - df["dep"]) / df["dep"]

    # ---- observation counts for the history filter ----
    df["n_obs_so_far"] = df.groupby("cert").cumcount() + 1

    # ---- merger / exit metadata ----
    exits, absorptions = merger_events(inst)
    exit_map = exits.set_index("cert")[["end_qidx", "was_acquired", "same_hc_successor"]]
    df = df.join(exit_map, on="cert")
    df["absorbed_next"] = (
        df.merge(
            absorptions.assign(qidx=absorptions["end_qidx"] - 1)[["cert", "qidx", "n_absorbed"]],
            on=["cert", "qidx"],
            how="left",
        )["n_absorbed"]
        .fillna(0)
        .to_numpy()
    )
    # Feature-side contamination flag: absorbed someone within the trailing 4 quarters.
    absorb_recent = absorptions.copy()
    recent_flags = []
    for lag in range(0, 4):
        tmp = absorb_recent.assign(qidx=absorb_recent["end_qidx"] + lag)[["cert", "qidx"]]
        recent_flags.append(tmp)
    recent = pd.concat(recent_flags).drop_duplicates()
    recent["absorbed_recent"] = 1
    df = df.merge(recent, on=["cert", "qidx"], how="left")
    df["absorbed_recent"] = df["absorbed_recent"].fillna(0).astype(int)

    # ---- de novo ----
    est = inst.drop_duplicates("CERT").set_index(
        inst.drop_duplicates("CERT")["CERT"].astype("int64")
    )
    df["est_qidx"] = df["cert"].map(_date_to_qidx(est["ESTYMD"]))
    df["age_quarters"] = df["qidx"] - df["est_qidx"]

    # ---- exclusion rules, applied in a fixed order so the audit is interpretable ----
    last_qidx = df["qidx"].max()
    # Quarters until this bank's own exit (NaN for survivors).
    df["quarters_to_exit"] = df["end_qidx"] - df["qidx"]
    rules = {
        "foreign_branch_charter (BKCLASS in NC/OI)": df["BKCLASS"].isin(EXCLUDED_BKCLASS),
        "no_next_quarter_row (bank exited or panel end)": df["dep_next"].isna(),
        "degenerate_base (deposits < $10M at T)": df["dep"] < MIN_DEPOSITS_USD_K,
        # Symmetric materiality: if T+1 deposits fall below the same floor we require at T,
        # the charter is winding down. 8 rows report DEP exactly 0 at T+1.
        "terminal_winddown (deposits < $10M at T+1)": df["dep_next"] < MIN_DEPOSITS_USD_K,
        # Intra-HC charter consolidation inside the deposit-transfer window.
        "intra_hc_consolidation (affiliate absorbs charter <=5q out)": (
            df["same_hc_successor"].fillna(False).astype(bool) & (df["quarters_to_exit"] <= 5)
        ),
        "insufficient_history (<5 quarterly obs)": df["n_obs_so_far"] < MIN_HISTORY_QUARTERS,
        "de_novo (<8 quarters old at T)": df["age_quarters"] < DE_NOVO_QUARTERS,
        "absorbed_another_bank_in_T+1 (inorganic jump)": df["absorbed_next"] > 0,
        # Hand-adjudicated residue. Two automatic discriminators were tried and rejected:
        # (a) same-quarter offsetting gain at an affiliate -- confounded, because a parent
        #     can be shedding deposits organically at the same time it receives a book;
        # (b) blanket exclusion of pre-merger quarters -- would have removed Silvergate
        #     2022Q4, the single most informative genuine run in the panel.
        # So extreme declines are surfaced to reports/extreme_declines_review.csv and
        # adjudicated by hand into a small, version-controlled exception list.
        "manually_adjudicated_structure_event": _manual_exclusion_mask(df),
    }
    audit_rows, remaining = [], pd.Series(True, index=df.index)
    for name, mask in rules.items():
        m = mask.fillna(False) if mask.dtype == bool else mask.astype(bool)
        newly = remaining & m
        audit_rows.append({"rule": name, "rows_removed": int(newly.sum())})
        remaining &= ~m
    audit = pd.DataFrame(audit_rows)
    audit.loc[len(audit)] = {"rule": "RETAINED", "rows_removed": int(remaining.sum())}

    # Breakdown of why rows vanished, for the README.
    vanished = df[df["dep_next"].isna() & (df["qidx"] < last_qidx)]
    reason = pd.DataFrame(
        {
            "reason": ["acquired (NEWCERT set)", "exited without successor", "no exit record"],
            "banks": [
                int(vanished["was_acquired"].fillna(False).sum()),
                int((vanished["was_acquired"] == False).sum()),  # noqa: E712
                int(vanished["was_acquired"].isna().sum()),
            ],
        }
    )

    labelled = df[remaining].copy()
    labelled["label"] = (labelled["dep_growth_next"] <= DRAWDOWN_THRESHOLD).astype(int)
    labelled["label_severe"] = (labelled["dep_growth_next"] <= SEVERE_THRESHOLD).astype(int)
    # The quarter the event actually happens in -- correct unit for seasonality reporting.
    labelled["event_quarter"] = labelled["quarter"].map(next_quarter)
    # Feature-side contamination flag (not an exclusion): an undetected structure event in
    # the trailing window makes trailing-growth features unreliable. Reported, and used for
    # a robustness re-run rather than silently dropped -- these rows are mostly negatives,
    # so dropping them would bias the base rate.
    labelled["structure_suspect"] = (
        (
            (labelled["absorbed_recent"] > 0)
            | (labelled.groupby("cert")["dep"].pct_change(fill_method=None).abs() > 0.25)
        )
        .fillna(False)
        .astype(int)
    )

    # The scoring set: latest quarter, which has no T+1 by construction.
    scoring = df[(df["qidx"] == last_qidx) & (df["dep"] >= MIN_DEPOSITS_USD_K)].copy()

    return labelled, {"audit": audit, "exit_reasons": reason, "scoring": scoring}


def base_rates(labelled: pd.DataFrame) -> pd.DataFrame:
    """Per-quarter base rate for both tiers."""
    g = labelled.groupby("quarter").agg(
        n_banks=("label", "size"),
        n_drawdown=("label", "sum"),
        n_severe=("label_severe", "sum"),
        median_growth=("dep_growth_next", "median"),
    )
    g["base_rate_pct"] = 100 * g["n_drawdown"] / g["n_banks"]
    g["severe_rate_pct"] = 100 * g["n_severe"] / g["n_banks"]
    return g.reset_index()
