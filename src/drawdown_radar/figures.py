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
