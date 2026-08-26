"""Build the JSON the results page reads.

Everything here comes from files the pipeline already writes: the feature
matrix under data/processed/ and the CSVs under reports/. Nothing is typed in
by hand, so the page cannot drift away from the backtest that produced it.

    make page-data      # or: .venv/bin/python scripts/make_page_data.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
OUT = ROOT / "docs" / "data"

# The bucket edges the U-shape is read off. Left-closed, so a bank at exactly
# zero growth lands in the safest bucket rather than the one below it.
EDGES = [-np.inf, -0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, np.inf]
LABELS = [
    "under -10%", "-10 to -5%", "-5 to -2%", "-2 to 0%",
    "0 to +2%", "+2 to +5%", "+5 to +10%", "over +10%",
]
# Ordered small to large so the picker reads in one direction.
SIZE_ORDER = ["<$300M", "$300M-1B", "$1-10B", "$10-100B", ">$100B"]


def u_shape() -> dict:
    """Next-quarter drawdown rate against this quarter's deposit growth."""
    df = pd.read_parquet(
        ROOT / "data/processed/dataset.parquet",
        columns=["dep_growth_1q", "label", "size_bucket"],
    ).dropna(subset=["dep_growth_1q", "label"])
    df = df.assign(bucket=pd.cut(df.dep_growth_1q, EDGES, labels=LABELS, right=False))

    def curve(frame: pd.DataFrame) -> dict:
        base = float(frame.label.mean())
        g = frame.groupby("bucket", observed=True).label.agg(["size", "mean"])
        return {
            "base_rate": base,
            "n": int(len(frame)),
            "buckets": [
                {
                    "label": str(b),
                    "n": int(row["size"]),
                    "rate": float(row["mean"]),
                    "lift": float(row["mean"] / base),
                }
                for b, row in g.iterrows()
            ],
        }

    bands = {"all banks": curve(df)}
    for name in SIZE_ORDER:
        part = df[df.size_bucket == name]
        if len(part):
            bands[name] = curve(part)
    return bands


def _rows(name: str) -> list[dict]:
    with (REPORTS / name).open() as fh:
        return list(csv.DictReader(fh))


def _num(row: dict, key: str):
    value = row.get(key, "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def models() -> dict:
    """Backtest, the out-of-time holdout, and the shuffled-label control.

    Pooled AUC is dropped rather than reported. The backtest names those
    columns INFLATED because a model that only knows the quarter scores well
    on them while being unable to rank inside any single quarter, which is the
    only ranking the alert list actually needs.
    """
    keep = (
        "precision_at_1pct", "recall_at_1pct", "lift_at_1pct",
        "precision_at_5pct", "recall_at_5pct", "lift_at_5pct",
        "roc_auc_within_quarter", "roc_auc_within_quarter_std",
        "lift_at_1pct_frac_above_1", "base_rate",
    )

    def pack(row: dict, name: str | None = None) -> dict:
        out = {k: _num(row, k) for k in keep}
        out["model"] = name or row.get("model", "")
        out["is_baseline"] = row.get("is_baseline", "") == "True"
        out["note"] = row.get("note", "")
        return out

    shuffled = _rows("shuffled_label_check.csv")[0]
    return {
        "backtest": [pack(r) for r in _rows("backtest_models.csv")],
        "out_of_time": [pack(r) for r in _rows("out_of_time.csv")],
        "shuffled": pack(shuffled, "labels shuffled (control)"),
    }




def alerts() -> list[dict]:
    """A quarter of real output with the banks anonymised.

    The scores are real and the reasons are the model's own, but this page is
    not the place to publish a named list: a public ranking of identifiable
    banks by deposit-flight risk reads as a claim about those banks rather
    than a demonstration of a method. The repository keeps the named file.
    """
    out = []
    for i, r in enumerate(_rows("alerts_2026Q1.csv"), start=1):
        out.append({
            "rank": i,
            "size_stratum": r["size_stratum"],
            "deposits_usd_m": _num(r, "total_deposits_usd_m"),
            "score": _num(r, "score"),
            "percentile": _num(r, "percentile_in_stratum"),
            "reason": r["reason"],
        })
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written = {
        "ushape.json": u_shape(),
        "models.json": models(),
        "alerts.json": alerts(),
    }
    for name, payload in written.items():
        path = OUT / name
        path.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1024:.1f} kB")


if __name__ == "__main__":
    main()
