"""Orchestration for `backtest` and `scorecards`. Writes every artifact the README cites."""

from __future__ import annotations

import pandas as pd

from . import ablation, audit, evaluate, figures, scorecards
from . import backtest as bt
from .backtest import Spec
from .config import PROCESSED, REPORTS


def _dataset() -> pd.DataFrame:
    p = PROCESSED / "dataset.parquet"
    if not p.exists():
        raise FileNotFoundError("Run `drawdown-radar build` first.")
    return pd.read_parquet(p)


def run_backtest(skip_ablations: bool = False) -> None:
    df = _dataset()
    feats = bt.structured_features()
    main = Spec("HistGB (structured)", "histgb", feats)

    print("=== models and baselines (walk-forward, 28 quarters) ===")
    rows, store = [], {}
    for spec in bt.default_specs():
        preds = bt.run_spec(df, spec)
        s = evaluate.summarize(preds)
        store[spec.name] = preds
        rows.append({"model": spec.name, "is_baseline": spec.is_baseline, "note": spec.note, **s})
        print(
            f"  {spec.name:36s} prec@5% {s['precision_at_5pct']:.3f} "
            f"lift {s['lift_at_5pct']:.2f}  AUC_wq {s['roc_auc_within_quarter']:.3f}"
        )
    results = pd.DataFrame(rows)
    results.to_csv(REPORTS / "backtest_models.csv", index=False)
    pd.to_pickle(store, PROCESSED / "preds.pkl")

    print("\n=== leakage check: labels shuffled within quarter ===")
    sh = bt.run_spec(audit.shuffled_label_check(df), main)
    s_sh = evaluate.summarize(sh)
    passed = s_sh["lift_at_5pct"] < 1.15 and abs(s_sh["roc_auc_within_quarter"] - 0.5) < 0.03
    print(
        f"  lift@5% {s_sh['lift_at_5pct']:.3f}  AUC_wq {s_sh['roc_auc_within_quarter']:.3f}  "
        f"-> {'PASS' if passed else 'FAIL'}"
    )
    pd.DataFrame([s_sh]).to_csv(REPORTS / "shuffled_label_check.csv", index=False)
    if not passed:
        raise AssertionError("shuffled-label test did not collapse to baseline -- leakage")

    print("\n=== out-of-time: trained on T<=2021Q3, predicting the SVB era ===")
    oot_rows = []
    for spec in [main, Spec("logit L1", "logit_l1", feats), *bt.default_specs()[1:4]]:
        p = bt.out_of_time(df, spec)
        if p.empty:
            continue
        s = evaluate.summarize(p)
        oot_rows.append({"model": spec.name, **s})
        print(
            f"  {spec.name:36s} prec@5% {s['precision_at_5pct']:.3f} lift {s['lift_at_5pct']:.2f}"
        )
    pd.DataFrame(oot_rows).to_csv(REPORTS / "out_of_time.csv", index=False)

    print("\n=== size-stratified precision@k ===")
    strat = []
    for nm in ["baseline: base rate (random rank)", "baseline: size only", "HistGB (structured)"]:
        s = evaluate.stratified_summary(store[nm])
        s["model"] = nm
        strat.append(s)
    strat_df = pd.concat(strat, ignore_index=True)
    strat_df.to_csv(REPORTS / "stratified.csv", index=False)
    print(
        strat_df[strat_df.k_frac == 0.05][
            ["model", "size_stratum", "mean_k", "precision", "lift"]
        ].to_string(index=False)
    )

    print("\n=== severity tiers ===")
    tiers = []
    for lbl, tag in [("label", "primary <=-5%"), ("label_severe", "severe <=-10%")]:
        p = bt.run_spec(df, main, label=lbl)
        s = evaluate.summarize(p.rename(columns={lbl: "label"}), label="label")
        tiers.append({"tier": tag, **s})
    pd.DataFrame(tiers).to_csv(REPORTS / "severity_tiers.csv", index=False)

    evaluate.calibration_table(store["HistGB (structured)"]).to_csv(
        REPORTS / "calibration.csv", index=False
    )
    figures.model_comparison_figure(results)
    figures.calibration_figure(evaluate.calibration_table(store["HistGB (structured)"]))
    figures.per_quarter_figure(store)

    if not skip_ablations:
        print("\n=== ablations ===")
        abl = ablation.run(df)
        sev = ablation.standalone_severe(df, verbose=False)
        abl.merge(sev, on="signal", how="left").to_csv(REPORTS / "ablations.csv", index=False)
    run_scorecards()


def run_scorecards() -> None:
    path = REPORTS / "ablations.csv"
    if not path.exists():
        raise FileNotFoundError("Run `drawdown-radar backtest` first (it writes ablations.csv).")
    abl = pd.read_csv(path)
    cards = scorecards.apply_verdicts(abl)
    headline = None
    mp = REPORTS / "backtest_models.csv"
    if mp.exists():
        m = pd.read_csv(mp)
        row = m[m["model"] == "HistGB (structured)"]
        if len(row):
            headline = row.iloc[0].to_dict()
    scorecards.write_markdown(cards, headline)
    if scorecards.write_readme_table(cards):
        print("README signal table regenerated from the registry")
    counts = cards["status"].value_counts().to_dict()
    print(f"scorecards written: {counts}")
    figures.signal_effect_figure(cards)


def run_text(verbose: bool = True) -> pd.DataFrame:
    """Does the text modality add INCREMENTAL lift over the graduated structured set?

    Evaluated inside the >$10B stratum on the **covered subset only**. Both arms are fitted and
    scored on identical rows, so the comparison isolates the text features rather than the
    difference between two populations -- the mistake that would make text look good simply
    because covered banks are larger.

    Coverage is 50 banks of ~4,350, so within-quarter top-k is thin (k=20% is ~10 alerts). The
    headline here is within-quarter AUC, which is stable at n=50/quarter; lift@20% is reported
    alongside and read with that caveat.
    """
    from .backtest import Spec, run_spec
    from .extraction import timing, validate
    from .registry import REGISTRY, Status

    df = _dataset()
    ext = validate.load_extractions()
    if "error" in ext:
        ext = ext[ext["error"].isna()]

    text_rows = timing.to_feature_rows(ext)
    if text_rows.empty:
        raise RuntimeError("no eligible text rows survived the timing guard")

    # `build_matrix` emits all-NaN placeholders for the text signals (the base panel has no
    # text), so drop them before merging the real values in under the same names -- otherwise
    # pandas suffixes both sides to _x/_y and the model silently trains on the empty copy.
    placeholders = [c for c in text_rows.columns if c in df.columns and c not in ("cert", "qidx")]
    df = df.drop(columns=placeholders)
    merged = df.merge(text_rows, on=["cert", "qidx"], how="left")
    covered = merged[merged["n_docs"].notna() & (merged["size_stratum"] == ">$10B")].copy()
    if verbose:
        print(
            f"text feature rows: {len(text_rows)} | covered bank-quarters in >$10B: {len(covered)}"
            f" across {covered['cert'].nunique()} banks, {covered['quarter'].nunique()} quarters"
        )
        print(f"  base rate on the covered subset: {100 * covered['label'].mean():.2f}%")

    graduated = [
        s.name for s in REGISTRY.values() if s.status is Status.GRADUATED and s.in_model
    ] or bt.structured_features()
    text_feats = [s.name for s in REGISTRY.values() if s.family == "unstructured-text"]

    first_test = (
        sorted(covered["quarter"].unique())[1] if covered["quarter"].nunique() > 1 else None
    )
    arms = {
        "graduated structured only": graduated,
        "structured + text": graduated + text_feats,
        "text only": text_feats,
    }
    rows = []
    for name, feats in arms.items():
        preds = run_spec(covered, Spec(name, "histgb", feats), first_test=first_test)
        if preds.empty:
            continue
        s = evaluate.summarize(preds)
        per_q = evaluate.within_quarter_at_k(preds, k_frac=0.20)
        rows.append(
            {
                "arm": name,
                "n_features": len(feats),
                "roc_auc_within_quarter": s["roc_auc_within_quarter"],
                "precision_at_20pct": per_q["precision"].mean() if len(per_q) else float("nan"),
                "lift_at_20pct": per_q["lift"].mean() if len(per_q) else float("nan"),
                "base_rate": s["base_rate"],
                "n_rows": s["n_rows"],
                "n_events": s["n_events"],
            }
        )
        if verbose:
            print(
                f"  {name:28s} AUC_wq {rows[-1]['roc_auc_within_quarter']:.4f}  "
                f"lift@20% {rows[-1]['lift_at_20pct']:.3f}"
            )
    out = pd.DataFrame(rows)
    if len(out) >= 2:
        base = out[out["arm"] == "graduated structured only"].iloc[0]
        both = out[out["arm"] == "structured + text"].iloc[0]
        out.attrs["incremental_auc"] = (
            both["roc_auc_within_quarter"] - base["roc_auc_within_quarter"]
        )
        out.attrs["incremental_lift"] = both["lift_at_20pct"] - base["lift_at_20pct"]
        if verbose:
            print(
                "\n  INCREMENTAL over graduated structured set: "
                f"AUC {out.attrs['incremental_auc']:+.4f}, "
                f"lift@20% {out.attrs['incremental_lift']:+.3f}"
            )
    out.to_csv(REPORTS / "text_incremental.csv", index=False)

    # Standalone lift per text flag, same protocol as every other candidate.
    solo = []
    for f in text_feats:
        preds = run_spec(covered, Spec(f"solo:{f}", "histgb", [f]), first_test=first_test)
        if preds.empty:
            continue
        s = evaluate.summarize(preds)
        per_q = evaluate.within_quarter_at_k(preds, k_frac=0.20)
        solo.append(
            {
                "signal": f,
                "standalone_auc_within_q": s["roc_auc_within_quarter"],
                "standalone_lift_20pct": per_q["lift"].mean() if len(per_q) else float("nan"),
            }
        )
    pd.DataFrame(solo).to_csv(REPORTS / "text_standalone.csv", index=False)
    return out
