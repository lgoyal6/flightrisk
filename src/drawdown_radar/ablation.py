"""Single-feature and leave-one-out ablations — the evidence behind every graduation verdict.

Two numbers per candidate:

* **standalone lift** — how well the signal ranks banks on its own, as a HistGB on that one
  column. Gradient boosting rather than logistic on purpose: several candidates are
  non-monotone (the persistence baseline ranks *worse than random* overall while its top
  percentile is strongly predictive), and a linear standalone fit would score those as
  worthless when they are not.

* **incremental lift** — the drop in the full model when this one signal is removed
  (leave-one-out). Positive delta means the signal carries information the other 24 do not.
  This is the number that matters: a candidate can look strong standalone and be redundant.

Both are computed with the same walk-forward protocol and the same within-quarter metric as
the headline results, so they are directly comparable.
"""

from __future__ import annotations

import pandas as pd

from . import signals  # noqa: F401
from .backtest import Spec, run_spec, structured_features
from .config import REPORTS
from .evaluate import summarize
from .registry import REGISTRY

METRIC = "lift_at_5pct"
SECONDARY = "precision_at_5pct"

# Text signals are judged separately, in `pipeline.run_text`, on the covered >$10B subset. They
# are all-NaN on the full panel (the base matrix has no text), so ablating them here would fit a
# model on an empty column and then hand a structured verdict to a signal that was never measured
# on structured terms.
SKIP_FAMILIES = {"unstructured-text"}


def _ablatable() -> list[str]:
    return [n for n, s in REGISTRY.items() if s.family not in SKIP_FAMILIES]


def standalone(data: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Rank banks using one signal at a time."""
    rows = []
    for name in _ablatable():
        sig = REGISTRY[name]
        preds = run_spec(data, Spec(f"solo:{name}", "histgb", [name]))
        if preds.empty:
            continue
        s = summarize(preds)
        rows.append(
            {
                "signal": name,
                "family": sig.family,
                "dumb": sig.is_dumb,
                "control": sig.is_control,
                "macro": sig.is_macro,
                "standalone_lift_5pct": s[METRIC],
                "standalone_precision_5pct": s[SECONDARY],
                "standalone_auc_within_q": s["roc_auc_within_quarter"],
                "standalone_lift_frac_folds_above_1": s["lift_at_5pct_frac_above_1"],
            }
        )
        if verbose:
            print(
                f"  solo {name:32s} lift@5% {s[METRIC]:6.3f}  auc {s['roc_auc_within_quarter']:.3f}"
            )
    return pd.DataFrame(rows)


def leave_one_out(data: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Full structured model minus one signal, versus the full structured model."""
    feats = structured_features()
    full = summarize(run_spec(data, Spec("full", "histgb", feats)))
    rows = []
    for name in feats:
        reduced = [f for f in feats if f != name]
        s = summarize(run_spec(data, Spec(f"minus:{name}", "histgb", reduced)))
        rows.append(
            {
                "signal": name,
                "incremental_lift_5pct": full[METRIC] - s[METRIC],
                "incremental_precision_5pct": full[SECONDARY] - s[SECONDARY],
                "incremental_auc_within_q": full["roc_auc_within_quarter"]
                - s["roc_auc_within_quarter"],
            }
        )
        if verbose:
            print(f"  -{name:32s} incremental lift {rows[-1]['incremental_lift_5pct']:+.4f}")
    out = pd.DataFrame(rows)
    out.attrs["full_model"] = full
    return out


def standalone_severe(data: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Same standalone ablation against the -10% tier.

    A candidate that ranks well at -5% but not at -10% is threshold-specific rather than a
    read on funding fragility, which belongs in the scorecard's stability column.
    """
    rows = []
    for name in _ablatable():
        preds = run_spec(data, Spec(f"solo10:{name}", "histgb", [name]), label="label_severe")
        if preds.empty:
            continue
        s = summarize(preds.rename(columns={"label_severe": "label"}), label="label")
        rows.append({"signal": name, "standalone_lift_5pct_severe": s[METRIC]})
        if verbose:
            print(f"  solo(-10%) {name:32s} lift@5% {s[METRIC]:6.3f}")
    return pd.DataFrame(rows)


def run(data: pd.DataFrame) -> pd.DataFrame:
    print("standalone ablations...")
    solo = standalone(data)
    print("leave-one-out ablations...")
    loo = leave_one_out(data)
    merged = solo.merge(loo, on="signal", how="left")
    merged.to_csv(REPORTS / "ablations.csv", index=False)
    return merged
