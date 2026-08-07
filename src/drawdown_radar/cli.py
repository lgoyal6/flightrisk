"""drawdown-radar CLI. The pipeline is these commands, not a notebook."""

from __future__ import annotations

import typer

app = typer.Typer(
    add_completion=False,
    help="Leading indicators of quarterly bank deposit drawdown.",
    no_args_is_help=True,
)


@app.command()
def pull(refresh: bool = typer.Option(False, help="Re-fetch instead of using the parquet cache.")):
    """Fetch and cache FDIC financials + institution metadata."""
    from .data import pull_all

    pull_all(refresh=refresh)


@app.command()
def build():
    """Labels, exclusion audit, as-of audit, registry integrity, base rates, figures."""
    from .build import run

    run()


@app.command()
def backtest(
    skip_ablations: bool = typer.Option(
        False, help="Skip the per-signal ablations (the slow part) and only run the models."
    ),
):
    """Walk-forward backtest: baselines, models, leakage checks, out-of-time stress test."""
    from .pipeline import run_backtest

    run_backtest(skip_ablations=skip_ablations)


@app.command()
def scorecards():
    """Regenerate signal graduation scorecards from the registry."""
    from .pipeline import run_scorecards

    run_scorecards()


@app.command()
def score(quarter: str = typer.Option("2026Q1", help="Quarter to score, e.g. 2026Q1")):
    """Emit the ranked alert list as JSON + CSV, stratified by bank size."""
    from .score import build_alerts, write

    alerts = build_alerts(quarter)
    csv, js = write(alerts, quarter)
    cols = ["bank", "size_stratum", "total_deposits_usd_m", "score", "percentile_in_stratum"]
    typer.echo(alerts[cols].to_string(index=False))
    typer.echo(f"\nwrote {csv}\nwrote {js}")


if __name__ == "__main__":
    app()
