"""Figure generation. Palette validated with the dataviz six-checks in light and dark.

Every figure renders twice (light/dark) so the README can serve the viewer's theme via
`<picture>`. Colors come from validated categorical slots 1 (blue) and 2 (orange); dark
steps are selected for the dark surface, not an automatic flip of the light ones.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .config import FIGURES  # noqa: E402

THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "grid": "#dedcd6",
        "s1": "#2a78d6",
        "s2": "#eb6834",
        "band": "#e34948",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#ffffff",
        "ink2": "#c3c2b7",
        "grid": "#3a3a38",
        "s1": "#3987e5",
        "s2": "#d95926",
        "band": "#e66767",
    },
}


def _style(ax, t: dict, title: str, ylabel: str, subtitle: str | None = None) -> None:
    ax.set_facecolor(t["surface"])
    ax.figure.set_facecolor(t["surface"])
    # Recessive grid and axes: the data should be the loudest thing on the page.
    ax.grid(True, axis="y", color=t["grid"], linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(t["grid"])
    ax.tick_params(colors=t["ink2"], labelsize=9, length=0)
    ax.set_ylabel(ylabel, color=t["ink2"], fontsize=10)
    ax.set_title(title, color=t["ink"], fontsize=13, fontweight="bold", loc="left", pad=32)
    if subtitle:
        ax.text(
            0,
            1.012,
            subtitle,
            transform=ax.transAxes,
            color=t["ink2"],
            fontsize=9.5,
            va="bottom",
        )


def base_rate_figure(base_rates: pd.DataFrame) -> list:
    """Base rate over time, both event tiers, with the rate-hiking window marked."""
    out = []
    for mode, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(11, 4.6), dpi=170)
        br = base_rates.copy()
        # Plot against the quarter the event lands in.
        x = range(len(br))
        labels = br["event_quarter"].tolist() if "event_quarter" in br else br["quarter"].tolist()

        ax.plot(x, br["base_rate_pct"], color=t["s1"], linewidth=2, label="drawdown ≤ −5%")
        ax.plot(x, br["severe_rate_pct"], color=t["s2"], linewidth=2, label="severe ≤ −10%")
        pooled = br["n_drawdown"].sum() / br["n_banks"].sum() * 100
        ax.axhline(pooled, color=t["ink2"], linewidth=1, linestyle=(0, (4, 3)), alpha=0.7)

        # Headroom for the legend so it never sits on the data.
        top = br["base_rate_pct"].max() * 1.28
        ax.set_ylim(0, top)
        ax.set_xlim(-0.8, len(br) - 0.2)

        # Shade the SVB-era holdout window so the regime is visible, not asserted.
        # Annotated after the data is drawn, so the y position is the real axis top.
        lo = next((i for i, q in enumerate(labels) if q == "2023Q1"), None)
        hi = next((i for i, q in enumerate(labels) if q == "2023Q4"), None)
        if lo is not None and hi is not None:
            ax.axvspan(lo - 0.5, hi + 0.5, color=t["band"], alpha=0.10, zorder=0)
            ax.text(
                (lo + hi) / 2,
                top * 0.965,
                "held-out\nSVB era",
                ha="center",
                va="top",
                fontsize=8.5,
                color=t["ink2"],
            )
        # Direct label, placed inside the axes so it cannot be clipped at the edge.
        ax.text(
            len(br) - 1.2,
            pooled + top * 0.018,
            f"pooled {pooled:.2f}%",
            color=t["ink2"],
            fontsize=8.5,
            ha="right",
            va="bottom",
        )

        step = 4
        ax.set_xticks(list(x)[::step])
        ax.set_xticklabels(labels[::step], rotation=0)
        _style(
            ax,
            t,
            "Deposit drawdown base rate by quarter",
            "% of banks",
            "Share of banks whose total deposits fell by the stated amount, quarter over quarter.",
        )
        leg = ax.legend(frameon=False, loc="upper left", fontsize=9.5, ncol=2)
        for txt in leg.get_texts():
            txt.set_color(t["ink2"])  # identity via the mark, not colored text
        fig.tight_layout()
        p = FIGURES / f"base_rate_{mode}.png"
        fig.savefig(p, facecolor=t["surface"])
        plt.close(fig)
        out.append(p)
    return out


def model_comparison_figure(results: pd.DataFrame) -> list:
    """precision@5% lift by model, baselines visually separated from models."""
    out = []
    for mode, t in THEMES.items():
        r = results.sort_values("lift_at_5pct")
        fig, ax = plt.subplots(figsize=(9.5, 4.4), dpi=170)
        colors = [t["s2"] if b else t["s1"] for b in r["is_baseline"]]
        y = range(len(r))
        ax.barh(list(y), r["lift_at_5pct"], color=colors, height=0.62)
        ax.axvline(1.0, color=t["ink2"], linewidth=1, linestyle=(0, (4, 3)))
        # Anchored to the top of the axes, not below the lowest bar, so it cannot land on the
        # x tick labels.
        ax.text(
            1.0,
            len(r) - 0.35,
            " random ranking",
            color=t["ink2"],
            fontsize=8.5,
            ha="left",
            va="center",
        )
        for i, v in zip(y, r["lift_at_5pct"], strict=False):
            ax.text(v + 0.08, i, f"{v:.2f}x", va="center", fontsize=9, color=t["ink2"])
        ax.set_yticks(list(y))
        ax.set_yticklabels(r["model"], fontsize=9)
        ax.set_xlim(0, r["lift_at_5pct"].max() * 1.16)
        _style(
            ax,
            t,
            "Within-quarter precision@5% lift over base rate",
            "",
            "Orange = baseline, blue = model. Macro-averaged over 28 walk-forward quarters.",
        )
        ax.grid(False, axis="y")
        ax.grid(True, axis="x", color=t["grid"], linewidth=0.8)
        fig.tight_layout()
        p = FIGURES / f"model_comparison_{mode}.png"
        fig.savefig(p, facecolor=t["surface"])
        plt.close(fig)
        out.append(p)
    return out


def calibration_figure(cal: pd.DataFrame) -> list:
    out = []
    for mode, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(5.4, 4.6), dpi=170)
        hi = max(cal["predicted"].max(), cal["observed"].max()) * 1.1
        ax.plot([0, hi], [0, hi], color=t["ink2"], linewidth=1, linestyle=(0, (4, 3)))
        ax.plot(
            cal["predicted"], cal["observed"], color=t["s1"], linewidth=2, marker="o", markersize=6
        )
        ax.set_xlim(0, hi)
        ax.set_ylim(0, hi)
        ax.set_xlabel("predicted probability", color=t["ink2"], fontsize=10)
        _style(
            ax,
            t,
            "Calibration by score decile",
            "observed event rate",
            "Dashed line is perfect calibration.",
        )
        fig.tight_layout()
        p = FIGURES / f"calibration_{mode}.png"
        fig.savefig(p, facecolor=t["surface"])
        plt.close(fig)
        out.append(p)
    return out


def per_quarter_figure(store: dict) -> list:
    """precision@5% per test quarter: model vs the strongest baseline. Stability, not an average."""
    from .evaluate import within_quarter_at_k

    out = []
    model = "HistGB (structured)"
    base = "baseline: size only"
    for mode, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(11, 4.4), dpi=170)
        m = within_quarter_at_k(store[model], k_frac=0.05).sort_values("quarter")
        b = within_quarter_at_k(store[base], k_frac=0.05).sort_values("quarter")
        x = range(len(m))
        ax.plot(x, m["precision"], color=t["s1"], linewidth=2, label="HistGB (structured)")
        ax.plot(x, b["precision"], color=t["s2"], linewidth=2, label="baseline: size only")
        ax.plot(
            x,
            m["base_rate"],
            color=t["ink2"],
            linewidth=1.2,
            linestyle=(0, (4, 3)),
            label="quarter base rate",
        )
        labels = m["quarter"].tolist()
        lo = next((i for i, q in enumerate(labels) if q == "2022Q3"), None)
        hi = next((i for i, q in enumerate(labels) if q == "2023Q3"), None)
        top = m["precision"].max() * 1.3
        ax.set_ylim(0, top)
        ax.set_xlim(-0.6, len(m) - 0.4)
        if lo is not None and hi is not None:
            ax.axvspan(lo - 0.5, hi + 0.5, color=t["band"], alpha=0.10, zorder=0)
            ax.text(
                (lo + hi) / 2,
                top * 0.96,
                "SVB era",
                ha="center",
                va="top",
                fontsize=8.5,
                color=t["ink2"],
            )
        ax.set_xticks(list(x)[::4])
        ax.set_xticklabels(labels[::4])
        _style(
            ax,
            t,
            "Precision@5% per test quarter",
            "precision",
            "Held up through the 2022-23 regime shift rather than only working there.",
        )
        leg = ax.legend(frameon=False, loc="upper left", fontsize=9, ncol=3)
        for txt in leg.get_texts():
            txt.set_color(t["ink2"])
        fig.tight_layout()
        p = FIGURES / f"precision_per_quarter_{mode}.png"
        fig.savefig(p, facecolor=t["surface"])
        plt.close(fig)
        out.append(p)
    return out


def signal_effect_figure(cards: pd.DataFrame) -> list:
    """Standalone lift for graduated and parked signals, killed ones shown for contrast."""
    out = []
    for mode, t in THEMES.items():
        c = cards.dropna(subset=["standalone_lift_5pct"]).copy()
        c = c.sort_values("standalone_lift_5pct")
        fig, ax = plt.subplots(figsize=(9.5, max(4.2, 0.26 * len(c))), dpi=170)
        cmap = {"GRADUATED": t["s1"], "PARKED": t["s2"], "KILLED": t["grid"]}
        colors = [cmap.get(s, t["grid"]) for s in c["status"]]
        y = range(len(c))
        ax.barh(list(y), c["standalone_lift_5pct"], color=colors, height=0.68)
        ax.axvline(1.0, color=t["ink2"], linewidth=1, linestyle=(0, (4, 3)))
        ax.set_yticks(list(y))
        ax.set_yticklabels(
            [f"{n}{' (null)' if d else ''}" for n, d in zip(c["signal"], c["dumb"], strict=False)],
            fontsize=8,
        )
        _style(
            ax,
            t,
            "Standalone within-quarter lift@5% by signal",
            "",
            "Blue = graduated, orange = parked, grey = killed. Dashed = random ranking.",
        )
        ax.grid(False, axis="y")
        ax.grid(True, axis="x", color=t["grid"], linewidth=0.8)
        fig.tight_layout()
        p = FIGURES / f"signal_effects_{mode}.png"
        fig.savefig(p, facecolor=t["surface"])
        plt.close(fig)
        out.append(p)
    return out
