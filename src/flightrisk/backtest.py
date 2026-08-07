"""Expanding-window walk-forward backtest.

**The training boundary.** A row at quarter T carries the outcome observed in T+1, so its
label is not knowable until T+1 closes. Standing at the end of T with features in hand, the
most recent row whose label is already revealed is T-1. Training therefore uses
`T <= test_quarter - 1`, never `T <= test_quarter`. Using rows at T itself would train on an
outcome that has not happened yet -- an off-by-one that is invisible in the metrics and
inflates them. `tests/test_backtest.py` pins this boundary.

All preprocessing is fitted inside the fold. Nothing is scaled, imputed, or winsorized with
statistics drawn from the test quarter or beyond.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import KBinsDiscretizer, SplineTransformer, StandardScaler

# Importing `signals` populates REGISTRY. Without it every feature list below silently comes
# back empty and the models fit on nothing.
from . import signals  # noqa: F401
from .config import quarter_index
from .registry import REGISTRY

MIN_TRAIN_QUARTERS = 12
FIRST_TEST_QUARTER = "2019Q1"
# SVB-era out-of-time block: events land in 2022Q4..2023Q4, i.e. feature rows T=2022Q3..2023Q3.
OOT_FIRST_T = "2022Q3"
OOT_LAST_T = "2023Q3"
OOT_TRAIN_MAX_T = "2021Q3"  # trained only on pre-2022 information


def structured_features() -> list[str]:
    """The default model's feature set: everything flagged `in_model`.

    Excludes the null tripwires, the macro covariates, and `state_identity` -- all registered
    so they are tested and reported, none of them modelling features.
    """
    return [s.name for s in REGISTRY.values() if s.in_model]


# The growth family, where the U-shaped relationship lives (see the README): next-quarter
# drawdown risk is elevated at BOTH tails of this quarter's deposit growth. A linear model
# cannot represent that with a single coefficient, which is the hypothesis the binned and
# spline variants test.
GROWTH_FAMILY = (
    "dep_growth_1q",
    "dep_growth_decel_4q",
    "dep_growth_vol_4q",
    "asset_dep_divergence",
)
N_GROWTH_BINS = 9


def _binned_growth_pipeline(features: list[str], seed: int, bin_all: bool = False):
    """Logit with quantile-binned one-hot growth features.

    Bin EDGES are fitted by `KBinsDiscretizer` inside this pipeline, so they are estimated
    from the training fold only. Computing quantile edges on the full panel would leak the
    test quarter's distribution into the encoding -- subtly, and invisibly in the metrics.
    """
    growth = [f for f in features if bin_all or f in GROWTH_FAMILY]
    linear = [f for f in features if f not in growth]
    binner = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            (
                "bin",
                KBinsDiscretizer(
                    n_bins=N_GROWTH_BINS,
                    strategy="quantile",
                    encode="onehot-dense",
                    quantile_method="linear",
                ),
            ),
        ]
    )
    scaler = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    blocks = [("binned", binner, growth)]
    if linear:
        blocks.append(("linear", scaler, linear))
    return Pipeline(
        [
            ("prep", ColumnTransformer(blocks)),
            (
                "clf",
                LogisticRegression(
                    l1_ratio=1.0, solver="liblinear", C=0.1, max_iter=3000, random_state=seed
                ),
            ),
        ]
    )


def _spline_growth_pipeline(features: list[str], seed: int):
    """Logit with a cubic B-spline basis on the growth family. Knots fitted per fold."""
    growth = [f for f in features if f in GROWTH_FAMILY]
    linear = [f for f in features if f not in growth]
    spline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("spline", SplineTransformer(n_knots=7, degree=3, knots="quantile")),
        ]
    )
    scaler = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    blocks = [("spline", spline, growth)]
    if linear:
        blocks.append(("linear", scaler, linear))
    return Pipeline(
        [
            ("prep", ColumnTransformer(blocks)),
            (
                "clf",
                LogisticRegression(
                    l1_ratio=1.0, solver="liblinear", C=0.1, max_iter=3000, random_state=seed
                ),
            ),
        ]
    )


def make_model(kind: str, seed: int = 0, features: list[str] | None = None):
    """Fold-local pipeline. Imputers/scalers/bin-edges are fitted per fold, never globally."""
    if kind == "logit_l1_binned_growth":
        return _binned_growth_pipeline(features or [], seed)
    if kind == "logit_l1_binned_all":
        return _binned_growth_pipeline(features or [], seed, bin_all=True)
    if kind == "logit_l1_spline_growth":
        return _spline_growth_pipeline(features or [], seed)
    if kind == "logit_l1":
        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                (
                    # l1_ratio=1.0 is pure L1. `penalty="l1"` is deprecated in sklearn 1.8+.
                    "clf",
                    LogisticRegression(
                        l1_ratio=1.0, solver="liblinear", C=0.1, max_iter=2000, random_state=seed
                    ),
                ),
            ]
        )
    if kind == "logit_plain":
        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("clf", LogisticRegression(max_iter=2000, random_state=seed)),
            ]
        )
    if kind == "histgb":
        # Handles NaN natively, so no imputer -- one less fold-local fitted transform.
        return HistGradientBoostingClassifier(
            max_depth=4,
            max_iter=200,
            learning_rate=0.06,
            min_samples_leaf=50,
            l2_regularization=1.0,
            early_stopping=False,
            random_state=seed,
        )
    raise ValueError(f"unknown model kind: {kind}")


@dataclass
class Spec:
    """One thing to evaluate: a named model over a named feature set."""

    name: str
    kind: str  # logit_l1 | logit_plain | histgb | raw
    features: list[str]
    sign: float = 1.0  # for kind='raw': +1 use as-is, -1 negate before ranking
    is_baseline: bool = False
    note: str = ""


def _folds(
    quarters: list[str], first_test: str, min_train_quarters: int = MIN_TRAIN_QUARTERS
) -> list[tuple[str, list[str]]]:
    """(test_quarter, train_quarters) with the label-revelation gap enforced.

    `min_train_quarters` is overridable because the default (12) is calibrated for the full
    40-quarter panel. Applied to a 16-quarter subset -- the text-coverage window -- it discards
    almost every fold and leaves an evaluation set with 3 events, which is not enough to conclude
    anything. Any override is reported alongside the result rather than applied silently.
    """
    qs = sorted(set(quarters), key=quarter_index)
    out = []
    for t in qs:
        ti = quarter_index(t)
        if quarter_index(t) < quarter_index(first_test):
            continue
        train = [q for q in qs if quarter_index(q) <= ti - 1]
        if len(train) < min_train_quarters:
            continue
        out.append((t, train))
    return out


def run_spec(
    data: pd.DataFrame,
    spec: Spec,
    first_test: str = FIRST_TEST_QUARTER,
    label: str = "label",
    min_train_quarters: int = MIN_TRAIN_QUARTERS,
) -> pd.DataFrame:
    """Walk forward, returning one scored row per (bank, test quarter)."""
    preds = []
    for test_q, train_qs in _folds(data["quarter"].tolist(), first_test, min_train_quarters):
        tr = data[data["quarter"].isin(train_qs)]
        te = data[data["quarter"] == test_q]
        if te.empty or tr[label].nunique() < 2:
            continue
        preds.append(_score_fold(tr, te, spec, label))
    return pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()


def _score_fold(tr: pd.DataFrame, te: pd.DataFrame, spec: Spec, label: str) -> pd.DataFrame:
    out = te[["cert", "NAME", "quarter", "size_stratum", label]].copy()
    if spec.kind == "raw":
        out["score"] = spec.sign * pd.to_numeric(te[spec.features[0]], errors="coerce").fillna(
            pd.to_numeric(tr[spec.features[0]], errors="coerce").median()
        )
    elif spec.kind == "random":
        # crc32, not hash(): Python salts string hashing per process (PYTHONHASHSEED), so
        # hash() made the random baseline shift between runs -- lift@5% moved 1.05 -> 0.99
        # across two invocations of the same code. A baseline that is not reproducible is not
        # a baseline.
        seed = zlib.crc32(te["quarter"].iloc[0].encode()) + int(
            quarter_index(te["quarter"].iloc[0])
        )
        rng = np.random.default_rng(seed)
        out["score"] = rng.random(len(te))
    else:
        if not spec.features:
            raise ValueError(
                f"spec '{spec.name}' has an empty feature list -- the registry was probably "
                f"not populated (import flightrisk.signals)"
            )
        model = make_model(spec.kind, features=spec.features)
        model.fit(tr[spec.features], tr[label])
        out["score"] = model.predict_proba(te[spec.features])[:, 1]
    out["model"] = spec.name
    return out


def default_specs() -> list[Spec]:
    """The three mandated baselines, a macro baseline, the two models, and two tripwires."""
    structured = structured_features()
    return [
        Spec(
            "baseline: base rate (random rank)",
            "random",
            [],
            is_baseline=True,
            note="empirical floor; precision@k should equal the base rate",
        ),
        Spec(
            "baseline: naive persistence",
            "raw",
            ["dep_growth_1q"],
            sign=-1.0,
            is_baseline=True,
            note="rank by last quarter's deposit decline -- the ranked form of "
            "'flagged if drawdown last quarter'",
        ),
        Spec(
            "baseline: size only",
            "logit_plain",
            ["log_assets"],
            is_baseline=True,
            note="logistic on log assets alone",
        ),
        Spec(
            "baseline: macro regime only",
            "logit_plain",
            ["macro_agg_dep_growth"],
            is_baseline=True,
            note="constant within a quarter -- inflates pooled metrics, cannot rank a quarter",
        ),
        Spec("logit L1 (structured)", "logit_l1", structured),
        Spec("HistGB (structured)", "histgb", structured),
        Spec(
            "HistGB + macro",
            "histgb",
            structured + [s.name for s in REGISTRY.values() if s.is_macro],
            note="does knowing the regime add anything the book does not already say?",
        ),
        Spec(
            "HistGB + dumb tripwires",
            "histgb",
            structured + [s.name for s in REGISTRY.values() if s.is_dumb],
            note="tripwire: adding three null features must not improve anything",
        ),
    ]


def out_of_time(data: pd.DataFrame, spec: Spec, label: str = "label") -> pd.DataFrame:
    """Train ONLY on pre-2022 information, then predict straight through the SVB era.

    No walk-forward refits inside the block: the point is to measure how a model built with no
    knowledge of the hiking cycle behaves when the regime changes under it.
    """
    tr = data[data["quarter"].map(quarter_index) <= quarter_index(OOT_TRAIN_MAX_T)]
    te = data[
        data["quarter"]
        .map(quarter_index)
        .between(quarter_index(OOT_FIRST_T), quarter_index(OOT_LAST_T))
    ]
    if tr.empty or te.empty or tr[label].nunique() < 2:
        return pd.DataFrame()
    return _score_fold(tr, te, spec, label)
