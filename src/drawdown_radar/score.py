"""The alert list — the artifact a GTM/risk team actually consumes.

Ranked **within size stratum**, not globally. An unstratified top-50 on this panel is a
small-bank list: 3,287 of 4,352 banks are under $1B, so the >$10B names — the M&T /
Silvergate-class events anyone actually cares about — never surface. Each stratum gets its own
quota and its own percentile.

Every row carries the three signals that pushed it up and a plain-English reason, so a human
can sanity-check the alert instead of trusting a number.
"""

from __future__ import annotations

import json
import math

import pandas as pd

from . import signals  # noqa: F401
from .backtest import make_model, structured_features
from .config import PROCESSED, REPORTS, quarter_index
from .evaluate import size_stratum
from .registry import REGISTRY, load_evidence

# Alerts per quarter per stratum. Sized so the whole list is workable in a quarter (~50).
STRATUM_QUOTA = {"<$1B": 25, "$1B-$10B": 15, ">$10B": 10}

# Plain-English templates, keyed by signal. Written for a reader who has never seen the model.
#
# A tuple is (template_when_negative, template_when_positive). Signals whose sign flips the
# meaning MUST use one: a single "deposits already fell {v:.1%}" template reported Bank of New
# York Mellon's +26.3% deposit INFLOW as a 26.3% fall. The sign-aware wording is also the more
# informative one, because a large inflow genuinely is a risk signal here -- see the U-shape in
# the README: banks with >+10% growth run a 3.0x drawdown rate, nearly matching big decliners.
PHRASING = {
    "dep_growth_1q": (
        "deposits already fell {vabs:.1%} last quarter",
        "deposits jumped {vabs:.1%} last quarter, and lumpy inflows tend to leave again",
    ),
    "dep_growth_decel_4q": (
        "deposit growth decelerated {vabs:.1%} versus its own trailing year",
        "deposit growth accelerated {vabs:.1%} versus its own trailing year",
    ),
    "dep_growth_vol_4q": "deposit balances are unusually volatile (σ {v:.1%} over 4 quarters)",
    "brokered_share": "brokered deposits are {v:.1%} of the book",
    "brokered_share_chg_4q": (
        "brokered share fell {vabs:.1%} over the past year",
        "brokered share rose {vabs:.1%} over the past year",
    ),
    "time_dep_share": "time deposits are {v:.1%} of funding",
    "time_dep_share_chg_4q": (
        "funding shifted {vabs:.1%} out of time deposits over the year",
        "funding shifted {vabs:.1%} into time deposits over the year",
    ),
    "core_dep_share": "core deposits are only {v:.1%} of the book",
    "core_dep_share_chg_4q": (
        "core deposit share fell {vabs:.1%} over the year",
        "core deposit share rose {vabs:.1%} over the year",
    ),
    "uninsured_dep_share": "uninsured deposits are {v:.1%} of the book",
    "uninsured_dep_share_chg_4q": (
        "uninsured share fell {vabs:.1%} over the year",
        "uninsured share rose {vabs:.1%} over the year",
    ),
    "noninterest_dep_share": "noninterest-bearing (operational) deposits are {v:.1%}",
    "noninterest_dep_share_chg_4q": (
        "operational balances shrank {vabs:.1%} as a share of the book over the year",
        "operational balances grew {vabs:.1%} as a share of the book over the year",
    ),
    "ltd_level": "loan-to-deposit ratio is {v:.0%}",
    "ltd_trend_4q": (
        "loan-to-deposit ratio fell {vabs:.1%} over the year",
        "loan-to-deposit ratio rose {vabs:.1%} over the year",
    ),
    "eq_assets": "equity is {v:.1%} of assets",
    "eq_assets_chg_4q": (
        "capital ratio fell {vabs:.1%} over the year",
        "capital ratio rose {vabs:.1%} over the year",
    ),
    "unrealized_afs_loss_to_eq": (
        "unrealized securities losses equal {vabs:.1%} of equity",
        "securities sit at an unrealized gain of {vabs:.1%} of equity",
    ),
    "unrealized_afs_loss_chg_4q": (
        "the securities mark deteriorated by {vabs:.1%} of equity over the year",
        "the securities mark improved by {vabs:.1%} of equity over the year",
    ),
    "nim_level": "net interest margin is {v:.2f}%",
    "nim_compression_4q": (
        "net interest margin compressed {vabs:.2f}pp over the year",
        "net interest margin widened {vabs:.2f}pp over the year",
    ),
    "asset_dep_divergence": (
        "deposits grew {vabs:.1%} faster than assets",
        "assets grew {vabs:.1%} faster than deposits",
    ),
    "prior_drawdown_1q": "had a drawdown last quarter",
    "prior_drawdown_count_4q": "had {v:.0f} drawdown quarter(s) in the past year",
    "log_assets": "the bank holds about ${assets_b:.1f}B in assets",
}


def _reason(row: pd.Series, contributors: list[tuple[str, float]]) -> str:
    """Plain-English rationale from the three most unusual signals for this bank."""
    parts = []
    for name, _ in contributors:
        tmpl = PHRASING.get(name)
        val = row.get(name)
        if tmpl is None or pd.isna(val):
            continue
        if isinstance(tmpl, tuple):  # sign changes the meaning -- pick the right wording
            tmpl = tmpl[0] if float(val) < 0 else tmpl[1]
        assets_b = 0.0
        if name == "log_assets":
            assets_b = math.exp(float(val)) / 1e6  # log($ thousands) -> $ billions
        parts.append(tmpl.format(v=val, vabs=abs(float(val)), assets_b=assets_b))
    if not parts:
        return "Ranked high on the graduated funding-fragility signals."
    return "Flagged because " + "; ".join(parts) + "."


def robust_z(target: pd.DataFrame, train: pd.DataFrame, feats: list[str]) -> pd.DataFrame:
    """How unusual each signal is for this bank, in robust (median/MAD) units.

    Baselines come from the TRAINING window only, so nothing about the scored quarter informs
    its own explanation. This is an attribution aid for a human reader, not a causal
    decomposition of the model — the README says so plainly.
    """
    med = train[feats].median()
    mad = (train[feats] - med).abs().median().replace(0, pd.NA)
    return ((target[feats] - med) / mad).fillna(0.0)


def build_alerts(quarter: str, top_n: dict | None = None) -> pd.DataFrame:
    """Train on everything with a revealed label, then score the requested quarter."""
    quota = top_n or STRATUM_QUOTA
    data = pd.read_parquet(PROCESSED / "dataset.parquet")
    feats = structured_features()

    qi = quarter_index(quarter)
    # Labels for rows at T are revealed at T+1, so training stops at T-1.
    train = data[data["quarter"].map(quarter_index) <= qi - 1]
    target = data[data["quarter"] == quarter]
    if target.empty:
        # The latest quarter has no label, so it is absent from the labelled dataset; rebuild
        # its feature row from the raw panel instead.
        target = _unlabelled_quarter(quarter, feats)
    if target.empty:
        raise ValueError(f"no rows available for {quarter}")
    if train.empty:
        raise ValueError(f"no training rows before {quarter}")

    model = make_model("histgb")
    model.fit(train[feats], train["label"])
    scored = target.copy()
    scored["score"] = model.predict_proba(target[feats])[:, 1]

    zt = robust_z(target, train, feats)

    if "size_stratum" not in scored or scored["size_stratum"].isna().all():
        scored["size_stratum"] = size_stratum(scored["ASSET"])

    rows = []
    for stratum, g in scored.groupby("size_stratum", observed=True):
        n = quota.get(str(stratum), 10)
        g = g.copy()
        g["percentile_in_stratum"] = 100 * g["score"].rank(pct=True)
        for _, r in g.nlargest(n, "score").iterrows():
            contrib = zt.loc[r.name].reindex(feats).abs().sort_values(ascending=False).head(3).index
            top3 = [(c, float(zt.loc[r.name, c])) for c in contrib]
            rows.append(
                {
                    "quarter": quarter,
                    "cert": int(r["cert"]),
                    "bank": r["NAME"],
                    "size_stratum": str(stratum),
                    "total_deposits_usd_m": round(float(r["dep"]) / 1000, 1)
                    if pd.notna(r.get("dep"))
                    else None,
                    "score": round(float(r["score"]), 5),
                    "percentile_in_stratum": round(float(r["percentile_in_stratum"]), 1),
                    "top_signals": [c for c, _ in top3],
                    "reason": _reason(r, top3),
                }
            )
    out = pd.DataFrame(rows).sort_values(["size_stratum", "score"], ascending=[True, False])
    return out.reset_index(drop=True)


def _unlabelled_quarter(quarter: str, feats: list[str]) -> pd.DataFrame:
    """Feature rows for a quarter whose outcome is not yet observable.

    The eligibility filters here MUST mirror the training-time exclusions in `labels.py`.
    Scoring a population the model was not trained on is how a $100k-deposit shell bank ends
    up at the top of an alert list -- the -5% rule is meaningless at that scale, which is
    exactly why training applies a $10M floor.
    """
    from .audit import build_matrix
    from .config import DE_NOVO_QUARTERS, EXCLUDED_BKCLASS, MIN_DEPOSITS_USD_K, MIN_HISTORY_QUARTERS
    from .data import load_institutions, load_panel
    from .labels import _date_to_qidx

    panel = load_panel()
    if quarter not in set(panel["quarter"]):
        return pd.DataFrame()
    qi = quarter_index(quarter)
    m = build_matrix(panel)
    base = panel[panel["quarter"] == quarter].copy()
    base["cert"] = base["CERT"].astype("int64")
    base["qidx"] = qi
    base["dep"] = pd.to_numeric(base["DEP"], errors="coerce")

    # Reporting history available up to and including this quarter.
    hist = panel.assign(cert=panel["CERT"].astype("int64"))
    hist = hist[hist["quarter"].map(lambda q: quarter_index(q)) <= qi]
    n_obs = hist.groupby("cert").size().rename("n_obs")
    base = base.join(n_obs, on="cert")

    inst = load_institutions().drop_duplicates("CERT")
    est = _date_to_qidx(inst.set_index(inst["CERT"].astype("int64"))["ESTYMD"])
    base["age_quarters"] = qi - base["cert"].map(est)

    eligible = (
        ~base["BKCLASS"].isin(EXCLUDED_BKCLASS)
        & (base["dep"] >= MIN_DEPOSITS_USD_K)
        & (base["n_obs"] >= MIN_HISTORY_QUARTERS)
        & (base["age_quarters"] >= DE_NOVO_QUARTERS)
    )
    dropped = int((~eligible).sum())
    if dropped:
        print(f"  {dropped} of {len(base)} banks excluded from scoring (same filters as training)")
    base = base[eligible]

    out = base.merge(m.drop(columns=["quarter"]), on=["cert", "qidx"], how="left")
    out["size_stratum"] = size_stratum(out["ASSET"])
    out["label"] = pd.NA
    return out


def write(alerts: pd.DataFrame, quarter: str) -> tuple[str, str]:
    csv = REPORTS / f"alerts_{quarter}.csv"
    js = REPORTS / f"alerts_{quarter}.json"
    alerts.to_csv(csv, index=False)
    load_evidence()  # re-attach graduation verdicts saved by `scorecards`
    payload = {
        "quarter": quarter,
        "generated_from": "HistGB on the graduated structured signal set",
        "stratified": True,
        "note": (
            "Ranked within size stratum. Scores are probabilities of a >=5% deposit decline "
            "in the following quarter; see reports/calibration.csv for reliability."
        ),
        "graduated_signals": [s.name for s in REGISTRY.values() if s.status.value == "GRADUATED"],
        "alerts": alerts.to_dict(orient="records"),
    }
    js.write_text(json.dumps(payload, indent=1, default=str))
    return str(csv), str(js)
