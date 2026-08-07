"""Walk-forward, registry, and metric tests. The fold boundary is the highest-stakes one."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from flightrisk import evaluate
from flightrisk.backtest import MIN_TRAIN_QUARTERS, Spec, _folds, structured_features
from flightrisk.config import quarter_index, quarter_range
from flightrisk.registry import REGISTRY, Status, check_integrity


# --------------------------------------------------------------- fold boundaries
def test_training_window_stops_one_quarter_before_the_test_quarter():
    """A row at T has its label revealed at T+1, so training must stop at T-1.

    Training through T would fit on an outcome that has not happened yet. The off-by-one is
    invisible in the metrics and inflates them, so it is pinned here.
    """
    qs = quarter_range("2016Q1", "2025Q4")
    folds = _folds(qs, "2019Q1")
    assert folds, "expected at least one fold"
    for test_q, train_qs in folds:
        assert max(quarter_index(q) for q in train_qs) == quarter_index(test_q) - 1
        assert test_q not in train_qs


def test_folds_are_expanding_not_rolling():
    folds = _folds(quarter_range("2016Q1", "2025Q4"), "2019Q1")
    sizes = [len(tr) for _, tr in folds]
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0]


def test_no_fold_trains_on_fewer_than_the_minimum_quarters():
    for _, train_qs in _folds(quarter_range("2016Q1", "2025Q4"), "2019Q1"):
        assert len(train_qs) >= MIN_TRAIN_QUARTERS


def test_first_test_quarter_is_respected():
    folds = _folds(quarter_range("2016Q1", "2025Q4"), "2020Q2")
    assert folds[0][0] == "2020Q2"


# --------------------------------------------------------------- registry
def test_registry_integrity_passes():
    check_integrity()


def test_every_signal_has_a_rationale_and_a_status():
    for s in REGISTRY.values():
        assert len(s.rationale.strip()) >= 15, s.name
        assert isinstance(s.status, Status), s.name
        assert s.family


def test_null_tripwires_exist_and_are_never_graduated():
    dumb = [s for s in REGISTRY.values() if s.is_dumb]
    assert len(dumb) >= 3, "the graduation framework needs null signals to be able to kill"
    for s in dumb:
        assert s.status is not Status.GRADUATED


def test_structured_feature_set_excludes_dumb_and_macro():
    feats = set(structured_features())
    for s in REGISTRY.values():
        if s.is_dumb or s.is_macro:
            assert s.name not in feats, f"{s.name} must not be in the structured set"
    assert len(feats) > 15


def test_spec_with_no_features_is_rejected_loudly():
    """An empty feature list means the registry was not imported -- fail, don't fit on nothing."""
    from flightrisk.backtest import _score_fold

    df = pd.DataFrame(
        {
            "cert": [1],
            "NAME": ["X"],
            "quarter": ["2020Q1"],
            "size_stratum": ["<$1B"],
            "label": [0],
            "f": [1.0],
        }
    )
    with pytest.raises(ValueError, match="empty feature list"):
        _score_fold(df, df, Spec("bad", "histgb", []), "label")


# --------------------------------------------------------------- metrics
def _preds(scores, labels, quarter="2020Q1"):
    return pd.DataFrame(
        {
            "quarter": quarter,
            "score": scores,
            "label": labels,
            "size_stratum": "<$1B",
            "cert": range(len(scores)),
        }
    )


def test_precision_at_k_is_computed_within_quarter_not_pooled():
    """Two quarters with very different base rates must be weighted equally."""
    q1 = _preds(np.linspace(1, 0, 100), [1] * 20 + [0] * 80, "2020Q1")  # 20% base
    q2 = _preds(np.linspace(1, 0, 100), [1] * 2 + [0] * 98, "2020Q2")  # 2% base
    per_q = evaluate.within_quarter_at_k(pd.concat([q1, q2]), k_frac=0.05)
    assert len(per_q) == 2
    assert set(per_q["quarter"]) == {"2020Q1", "2020Q2"}


def test_perfect_ranking_gives_precision_one_and_lift_above_one():
    p = _preds(np.linspace(1, 0, 200), [1] * 10 + [0] * 190)
    st = evaluate.within_quarter_at_k(p, k_frac=0.05).iloc[0]
    assert st["precision"] == pytest.approx(1.0)
    assert st["lift"] > 1


def test_constant_score_has_no_ranking_power():
    """A feature constant within a quarter cannot rank inside it -- the macro baseline case."""
    rng = np.random.default_rng(0)
    labels = rng.random(2000) < 0.05
    p = _preds(np.ones(2000), labels.astype(int))
    st = evaluate.within_quarter_at_k(p, k_frac=0.05).iloc[0]
    assert st["lift"] == pytest.approx(1.0, abs=0.6)


def test_quarters_with_no_events_are_skipped_not_counted_as_zero():
    p = _preds(np.linspace(1, 0, 50), [0] * 50)
    assert evaluate.within_quarter_at_k(p, k_frac=0.05).empty


def test_size_stratum_boundaries():
    s = evaluate.size_stratum(pd.Series([500_000, 5_000_000, 50_000_000]))
    assert list(s) == ["<$1B", "$1B-$10B", ">$10B"]


def test_shuffled_labels_preserve_the_per_quarter_base_rate():
    """Shuffling globally would also destroy the regime structure and make the test too easy."""
    from flightrisk.audit import shuffled_label_check

    rng = np.random.default_rng(1)
    df = pd.DataFrame(
        {
            "quarter": ["2020Q1"] * 500 + ["2020Q2"] * 500,
            "label": np.concatenate([rng.random(500) < 0.02, rng.random(500) < 0.20]).astype(int),
        }
    )
    out = shuffled_label_check(df, seed=3)
    before = df.groupby("quarter")["label"].mean()
    after = out.groupby("quarter")["label"].mean()
    pd.testing.assert_series_equal(before, after)
