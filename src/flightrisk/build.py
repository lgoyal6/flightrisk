"""Build step: labels + audits + base-rate reporting.

Feature construction and the as-of audit attach here in the next phase; this module already
owns the artifacts that must exist before any model is allowed to run.
"""

from __future__ import annotations

import pandas as pd

from . import figures
from .audit import asof_audit, build_matrix
from .config import PROCESSED, REPORTS, next_quarter
from .data import load_institutions, load_panel
from .evaluate import size_stratum
from .labels import base_rates, build_labels, write_review_file
from .registry import REGISTRY, check_integrity


def _size_bucket(assets: pd.Series) -> pd.Series:
    bins = [0, 300e3, 1e6, 10e6, 100e6, 1e12]
    names = ["<$300M", "$300M-1B", "$1-10B", "$10-100B", ">$100B"]
    return pd.cut(pd.to_numeric(assets, errors="coerce"), bins=bins, labels=names)


def run(verbose: bool = True) -> pd.DataFrame:
    panel, inst = load_panel(), load_institutions()
    labelled, extra = build_labels(panel, inst)

    labelled["size_bucket"] = _size_bucket(labelled["ASSET"])
    labelled.to_parquet(PROCESSED / "labels.parquet", index=False)

    # Base rates indexed by the quarter the event lands in.
    br = (
        labelled.groupby("event_quarter")
        .agg(
            n_banks=("label", "size"),
            n_drawdown=("label", "sum"),
            n_severe=("label_severe", "sum"),
        )
        .reset_index()
    )
    br["base_rate_pct"] = 100 * br["n_drawdown"] / br["n_banks"]
    br["severe_rate_pct"] = 100 * br["n_severe"] / br["n_banks"]
    br.to_csv(PROCESSED / "base_rates_event.csv", index=False)
    base_rates(labelled).to_csv(PROCESSED / "base_rates.csv", index=False)
    extra["audit"].to_csv(REPORTS / "exclusion_audit.csv", index=False)
    write_review_file(labelled)
    figures.base_rate_figure(br)

    by_size = labelled.groupby("size_bucket", observed=True).agg(
        rows=("label", "size"), banks=("cert", "nunique"), rate=("label", "mean")
    )
    by_size["base_rate_pct"] = (100 * by_size["rate"]).round(2)
    by_size[["rows", "banks", "base_rate_pct"]].to_csv(REPORTS / "base_rate_by_size.csv")

    # ---- registry integrity + as-of audit run on EVERY build, not on request ----
    check_integrity()
    print(f"registry integrity: OK ({len(REGISTRY)} signals)")
    audit_report = asof_audit(panel)
    audit_report.to_csv(REPORTS / "asof_audit.csv", index=False)

    # ---- assemble the modelling dataset ----
    matrix = build_matrix(panel)
    dataset = labelled.merge(matrix.drop(columns=["quarter"]), on=["cert", "qidx"], how="left")
    dataset["size_stratum"] = size_stratum(dataset["ASSET"])
    dataset.to_parquet(PROCESSED / "dataset.parquet", index=False)

    if verbose:
        print(f"raw panel            : {len(panel):,} bank-quarters")
        print(extra["audit"].to_string(index=False))
        print()
        print(f"labelled rows        : {len(labelled):,}")
        print(f"banks                : {labelled['cert'].nunique():,}")
        print(f"quarters (T)         : {labelled['quarter'].min()} .. {labelled['quarter'].max()}")
        print(
            f"base rate  <=-5%     : {100 * labelled['label'].mean():.2f}% "
            f"({int(labelled['label'].sum()):,} events)"
        )
        print(
            f"base rate <=-10%     : {100 * labelled['label_severe'].mean():.2f}% "
            f"({int(labelled['label_severe'].sum()):,} events)"
        )
        print(f"scoring quarter ready: {next_quarter(labelled['quarter'].max())}")
    return labelled
