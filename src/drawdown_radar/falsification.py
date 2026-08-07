"""Two falsification experiments, each attacking a claim this project makes.

**1. Recover the logit.** Claim under attack: "gradient boosting beats the linear model."
If the GBM's margin is really just the U-shaped deposit-growth relationship, then handing
that shape to the linear model -- quantile bins or a spline basis, edges fitted inside each
training fold -- should close most of the gap. If it does, the honest finding is "the
nonlinearity was the alpha, not the model class."

**2. Unseen-entity falsification.** Claim under attack: "the model reads funding structure."
The generalized version of the state-code lesson: quasi-stable per-bank ratios could let a
tree memorise entity-level base rates instead. So evaluate on banks the model has never
ranked before. If lift holds on first-appearance banks, the signal is structural. If it
collapses, it is partly a roster.

Pooling note: the first-appearance cohort is far too thin for per-quarter top-k (k would be
0 or 1). So scores are converted to their **within-quarter percentile** first -- which
preserves the regime-neutral ranking discipline -- and only then pooled across quarters.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from . import backtest as bt
from .backtest import Spec, out_of_time, run_spec, structured_features
from .config import PROCESSED, REPORTS
from .evaluate import summarize

# --------------------------------------------------------------------------- 1


def logit_variants() -> list[Spec]:
    feats = structured_features()
    return [
        Spec("logit L1 (linear)", "logit_l1", feats, note="reference: one coefficient per signal"),
        Spec(
            "logit L1 + binned growth",
            "logit_l1_binned_growth",
            feats,
            note=f"{bt.N_GROWTH_BINS} quantile bins, one-hot, on {len(bt.GROWTH_FAMILY)} growth "
            "signals; edges fitted per fold",
        ),
        Spec(
            "logit L1 + spline growth",
            "logit_l1_spline_growth",
            feats,
            note="cubic B-spline basis on the growth family; knots fitted per fold",
        ),
        Spec(
            "logit L1 + binned (all signals)",
            "logit_l1_binned_all",
            feats,
            note="every signal quantile-binned -- the most generous version of the linear model",
        ),
        Spec("HistGB (structured)", "histgb", feats, note="ceiling"),
    ]


def recover_logit(data: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Walk-forward and out-of-time for each linear variant, against the GBM ceiling."""
    rows = []
    for spec in logit_variants():
        wf = summarize(run_spec(data, spec))
        oot_preds = out_of_time(data, spec)
        oot = summarize(oot_preds) if not oot_preds.empty else {}
        rows.append(
            {
                "model": spec.name,
                "note": spec.note,
                "precision_at_1pct": wf["precision_at_1pct"],
                "precision_at_5pct": wf["precision_at_5pct"],
                "lift_at_5pct": wf["lift_at_5pct"],
                "roc_auc_within_quarter": wf["roc_auc_within_quarter"],
                "oot_precision_at_1pct": oot.get("precision_at_1pct"),
                "oot_precision_at_5pct": oot.get("precision_at_5pct"),
                "oot_lift_at_5pct": oot.get("lift_at_5pct"),
                "oot_roc_auc_within_quarter": oot.get("roc_auc_within_quarter"),
            }
        )
        if verbose:
            print(
                f"  {spec.name:34s} lift {rows[-1]['lift_at_5pct']:.3f}  "
                f"AUC {rows[-1]['roc_auc_within_quarter']:.4f}  "
                f"OOT lift {rows[-1]['oot_lift_at_5pct'] or float('nan'):.3f}"
            )
    out = pd.DataFrame(rows)

    # How much of the linear->GBM gap does each variant close?
    base = out[out["model"] == "logit L1 (linear)"].iloc[0]
    ceil = out[out["model"] == "HistGB (structured)"].iloc[0]
    for metric in ("lift_at_5pct", "roc_auc_within_quarter", "oot_lift_at_5pct"):
        span = float(ceil[metric]) - float(base[metric])
        out[f"gap_closed_{metric}"] = (
            ((out[metric].astype(float) - float(base[metric])) / span) if span else np.nan
        )
    out.to_csv(REPORTS / "recover_logit.csv", index=False)
    return out


# --------------------------------------------------------------------------- 2


def within_quarter_percentile(preds: pd.DataFrame, score: str = "score") -> pd.Series:
    """Rank inside the quarter, so scores from different quarters are comparable."""
    return preds.groupby("quarter")[score].rank(pct=True)


def entity_cohorts(data: pd.DataFrame, first_test: str = bt.FIRST_TEST_QUARTER) -> pd.DataFrame:
    """Label each (bank, test quarter) by whether the model could have seen that bank before.

    `unseen` means the CERT has NO row in the training window for that fold -- the model has
    never ranked this entity. Because the panel requires 5 quarterly observations before a row
    is labellable, these are banks that just crossed that threshold or the de novo filter.
    """
    rows = []
    for test_q, train_qs in bt._folds(data["quarter"].tolist(), first_test):
        tr = data[data["quarter"].isin(train_qs)]
        te = data[data["quarter"] == test_q]
        seen_certs = set(tr["cert"].unique())
        pos_certs = set(tr.loc[tr["label"] == 1, "cert"].unique())
        rows.append(
            pd.DataFrame(
                {
                    "cert": te["cert"].to_numpy(),
                    "quarter": test_q,
                    "unseen_entity": ~te["cert"].isin(seen_certs).to_numpy(),
                    "no_prior_positive": ~te["cert"].isin(pos_certs).to_numpy(),
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def _cohort_stats(g: pd.DataFrame, k_frac: float = 0.05) -> dict:
    n, n_pos = len(g), int(g["label"].sum())
    if n < 20 or n_pos == 0:
        return {"n": n, "n_pos": n_pos}
    k = max(1, int(np.ceil(k_frac * n)))
    hits = int(g.nlargest(k, "pctile")["label"].sum())
    base = n_pos / n
    return {
        "n": n,
        "n_pos": n_pos,
        "base_rate": base,
        "k": k,
        "precision_at_5pct": hits / k,
        "lift_at_5pct": (hits / k) / base,
        "roc_auc": roc_auc_score(g["label"], g["pctile"]) if g["label"].nunique() == 2 else np.nan,
    }


def unseen_entity(
    data: pd.DataFrame, preds: pd.DataFrame | None = None, verbose: bool = True
) -> pd.DataFrame:
    """Does lift hold on entities the model has never ranked before?"""
    if preds is None:
        preds = run_spec(data, Spec("HistGB (structured)", "histgb", structured_features()))
    preds = preds.copy()
    preds["pctile"] = within_quarter_percentile(preds)

    cohorts = entity_cohorts(data)
    m = preds.merge(cohorts, on=["cert", "quarter"], how="left")

    rows = []
    for cut, col in [
        ("entity never in training", "unseen_entity"),
        ("no prior positive", "no_prior_positive"),
    ]:
        for flag, tag in [(True, "cohort (a): yes"), (False, "cohort (b): no")]:
            g = m[m[col] == flag]
            rows.append({"cut": cut, "cohort": tag, **_cohort_stats(g)})
            if verbose:
                st = rows[-1]
                print(
                    f"  {cut:26s} {tag:16s} n={st['n']:6d} pos={st['n_pos']:5d} "
                    + (
                        f"lift {st['lift_at_5pct']:.3f} AUC {st['roc_auc']:.4f}"
                        if "lift_at_5pct" in st
                        else "(too thin to evaluate)"
                    )
                )
    out = pd.DataFrame(rows)
    out.to_csv(REPORTS / "unseen_entity.csv", index=False)

    # Per-quarter counts, so the thinness of cohort (a) is visible rather than asserted.
    per_q = (
        m[m["unseen_entity"]]
        .groupby("quarter")
        .agg(unseen_banks=("cert", "size"), events=("label", "sum"))
        .reset_index()
    )
    per_q.to_csv(REPORTS / "unseen_entity_per_quarter.csv", index=False)
    if verbose and len(per_q):
        print(
            "  first-appearance banks per test quarter: median "
            f"{per_q['unseen_banks'].median():.0f}, min {per_q['unseen_banks'].min()}, "
            f"max {per_q['unseen_banks'].max()}"
        )
    return out


def run(verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = pd.read_parquet(PROCESSED / "dataset.parquet")
    print("=== 1. recover the logit: was the nonlinearity the alpha? ===")
    rl = recover_logit(data, verbose)
    print("\n=== 2. unseen-entity falsification: structure or roster? ===")
    ue = unseen_entity(data, verbose=verbose)
    return rl, ue


__all__ = ["recover_logit", "unseen_entity", "entity_cohorts", "within_quarter_percentile", "run"]
