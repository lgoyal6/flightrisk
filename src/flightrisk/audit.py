"""As-of correctness audit, wired into `build` so it runs on every pipeline execution.

The test is behavioural rather than a code review: rebuild every feature from a panel
TRUNCATED at quarter Q, then require the values for rows at Q to be bit-identical to the
full-panel build. Any feature that reaches forward in time -- a centered window, a negative
shift, a full-sample mean, a scaler fit on everything -- changes when the future is removed
and is caught here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import signals  # noqa: F401  (import registers all candidates)
from .features import prepare
from .registry import REGISTRY


def build_matrix(panel: pd.DataFrame) -> pd.DataFrame:
    """Apply every registered signal to a prepared panel."""
    prepped = prepare(panel)
    out = prepped[["cert", "qidx", "quarter"]].copy()
    for name, sig in REGISTRY.items():
        out[name] = pd.to_numeric(sig.compute(prepped), errors="coerce")
    return out


class AsOfViolation(AssertionError):
    """Raised when a feature's value at T changes once future quarters are removed."""


def asof_audit(
    panel: pd.DataFrame, cutoffs: list[str] | None = None, verbose: bool = True
) -> pd.DataFrame:
    """Compare full-panel features at Q against features built from data <= Q only."""
    full = build_matrix(panel)
    qidx_of = dict(zip(full["quarter"], full["qidx"], strict=False))
    cutoffs = cutoffs or ["2018Q2", "2020Q4", "2022Q4", "2023Q2", "2025Q2"]
    names = list(REGISTRY.keys())
    rows = []

    for q in cutoffs:
        qi = qidx_of.get(q)
        if qi is None:
            continue
        truncated = build_matrix(panel[panel["quarter"].map(qidx_of).le(qi)])
        a = full[full["qidx"] == qi].set_index("cert")[names].sort_index()
        b = truncated[truncated["qidx"] == qi].set_index("cert")[names].sort_index()
        common = a.index.intersection(b.index)
        a, b = a.loc[common], b.loc[common]
        for n in names:
            x, y = a[n].to_numpy(dtype=float), b[n].to_numpy(dtype=float)
            both_nan = np.isnan(x) & np.isnan(y)
            equal = both_nan | np.isclose(x, y, rtol=0, atol=0, equal_nan=True)
            n_bad = int((~equal).sum())
            rows.append(
                {"cutoff": q, "signal": n, "rows_compared": len(common), "mismatches": n_bad}
            )

    report = pd.DataFrame(rows)
    bad = report[report["mismatches"] > 0]
    if len(bad):
        detail = "\n  ".join(
            f"{r.signal} @ {r.cutoff}: {r.mismatches}/{r.rows_compared} rows differ"
            for r in bad.itertuples()
        )
        raise AsOfViolation(
            "features changed when future quarters were removed -- they use T+1 information:"
            f"\n  {detail}"
        )
    if verbose:
        print(
            f"as-of audit: {len(names)} signals x {len(cutoffs)} cutoffs, "
            f"{int(report['rows_compared'].sum()):,} row-comparisons, 0 mismatches"
        )
    return report


def shuffled_label_check(labelled: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Permute labels WITHIN each quarter, preserving the per-quarter base rate.

    Shuffling globally would also destroy the regime structure, which makes the test too easy
    to pass. Permuting inside a quarter leaves the base rate untouched, so any surviving
    performance is genuine leakage rather than the model learning which quarter it is.
    """
    rng = np.random.default_rng(seed)
    out = labelled.copy()
    out["label"] = (
        out.groupby("quarter")["label"].transform(lambda s: rng.permutation(s.to_numpy()))
    ).astype(int)
    return out
