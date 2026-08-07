"""Metrics.

The headline metric is **within-quarter** precision@k / recall@k, macro-averaged over test
quarters. That is not a stylistic choice: the base rate moves from 1.16% (2020Q2, COVID
stimulus inflows) to 12.86% (2022Q4, the hiking cycle), so any feature correlated with the
macro cycle lets a model score well by inferring *which quarter it is*. Pooled metrics reward
that; ranking inside a quarter cannot. It is also the operational question -- "given this
quarter's book, who do we call?" -- because an alert list is worked within a quarter.

Pooled metrics are still reported, explicitly labelled as the inflated view. The
`macro_only` baseline exists to quantify that inflation: it is constant within a quarter, so
its within-quarter lift is 1.00 by construction while its pooled AUC is well above chance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

K_FRACTIONS = (0.01, 0.05)
# Strata chosen so the >$10B bucket -- where the M&T / Silvergate-class events live -- is
# evaluated on its own instead of being buried by ~3,900 community banks.
SIZE_STRATA = ("<$1B", "$1B-$10B", ">$10B")


def size_stratum(assets_thousands: pd.Series) -> pd.Series:
    return pd.cut(
        pd.to_numeric(assets_thousands, errors="coerce"),
        bins=[0, 1e6, 1e7, 1e12],
        labels=list(SIZE_STRATA),
    )


def _topk_stats(g: pd.DataFrame, k_frac: float, score: str) -> dict | None:
    n = len(g)
    n_pos = int(g["label"].sum())
    if n < 10 or n_pos == 0:
        return None
    k = max(1, int(np.ceil(k_frac * n)))
    top = g.nlargest(k, score)
    hits = int(top["label"].sum())
    base = n_pos / n
    return {
        "n": n,
        "k": k,
        "n_pos": n_pos,
        "precision": hits / k,
        "recall": hits / n_pos,
        "base_rate": base,
        "lift": (hits / k) / base if base > 0 else np.nan,
    }


def within_quarter_at_k(
    preds: pd.DataFrame, score: str = "score", k_frac: float = 0.05, by: str | None = None
) -> pd.DataFrame:
    """Per-quarter (optionally per-stratum) top-k stats. One row per evaluation group."""
    keys = ["quarter"] + ([by] if by else [])
    rows = []
    for key, g in preds.groupby(keys, observed=True):
        st = _topk_stats(g, k_frac, score)
        if st is None:
            continue
        key = key if isinstance(key, tuple) else (key,)
        rows.append({**dict(zip(keys, key, strict=False)), **st})
    return pd.DataFrame(rows)


def summarize(preds: pd.DataFrame, score: str = "score", label: str = "label") -> dict:
    """Headline (within-quarter, macro-averaged) plus the pooled view, clearly separated."""
    out: dict[str, float] = {}
    for kf in K_FRACTIONS:
        per_q = within_quarter_at_k(preds, score, kf)
        tag = f"{int(kf * 100)}pct"
        out[f"precision_at_{tag}"] = per_q["precision"].mean()
        out[f"recall_at_{tag}"] = per_q["recall"].mean()
        out[f"lift_at_{tag}"] = per_q["lift"].mean()
        # Fold-to-fold stability: a signal that only works in 2023 is a regime artifact.
        out[f"precision_at_{tag}_std"] = per_q["precision"].std()
        out[f"lift_at_{tag}_frac_above_1"] = float((per_q["lift"] > 1).mean())

    # Within-quarter ROC-AUC, macro-averaged: regime-neutral discrimination.
    aucs = []
    for _q, g in preds.groupby("quarter"):
        if g[label].nunique() == 2:
            aucs.append(roc_auc_score(g[label], g[score]))
    out["roc_auc_within_quarter"] = float(np.mean(aucs)) if aucs else np.nan
    out["roc_auc_within_quarter_std"] = float(np.std(aucs)) if aucs else np.nan

    # Pooled -- the INFLATED view, reported for comparison only.
    if preds[label].nunique() == 2:
        out["pooled_roc_auc_INFLATED"] = roc_auc_score(preds[label], preds[score])
        out["pooled_pr_auc_INFLATED"] = average_precision_score(preds[label], preds[score])
    out["base_rate"] = float(preds[label].mean())
    out["n_rows"] = int(len(preds))
    out["n_events"] = int(preds[label].sum())
    return out


def stratified_summary(preds: pd.DataFrame, score: str = "score") -> pd.DataFrame:
    """precision@k within (quarter, size stratum), macro-averaged per stratum."""
    rows = []
    for kf in K_FRACTIONS:
        per = within_quarter_at_k(preds, score, kf, by="size_stratum")
        if per.empty:
            continue
        agg = per.groupby("size_stratum", observed=True).agg(
            quarters=("precision", "size"),
            mean_k=("k", "mean"),
            precision=("precision", "mean"),
            recall=("recall", "mean"),
            base_rate=("base_rate", "mean"),
            lift=("lift", "mean"),
        )
        agg["k_frac"] = kf
        rows.append(agg.reset_index())
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def calibration_table(preds: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    """Predicted vs observed event rate by score decile."""
    p = preds.copy()
    p["bin"] = pd.qcut(p["score"].rank(method="first"), bins, labels=False)
    return (
        p.groupby("bin")
        .agg(n=("label", "size"), predicted=("score", "mean"), observed=("label", "mean"))
        .reset_index()
    )


def operating_point(preds: pd.DataFrame, flag_col: str) -> dict:
    """Precision/recall of a binary rule at its own natural threshold (no k needed)."""
    f = preds[flag_col].fillna(0) > 0
    tp = int((f & (preds["label"] == 1)).sum())
    return {
        "flagged": int(f.sum()),
        "flagged_pct": float(f.mean()),
        "precision": tp / max(int(f.sum()), 1),
        "recall": tp / max(int(preds["label"].sum()), 1),
    }
