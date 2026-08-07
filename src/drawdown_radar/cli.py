"""drawdown-radar CLI. The pipeline is these commands, not a notebook."""

from __future__ import annotations

import typer

app = typer.Typer(
    add_completion=False,
    help="Leading indicators of quarterly bank deposit drawdown.",
    no_args_is_help=True,
)

_PENDING = {
    "backtest": "phase 6 (walk-forward)",
    "scorecards": "phase 9 (signal graduation)",
    "score": "phase 10 (alert list)",
}


@app.command()
def pull(refresh: bool = typer.Option(False, help="Re-fetch instead of using the parquet cache.")):
    """Fetch and cache FDIC financials + institution metadata."""
    from .data import pull_all

    pull_all(refresh=refresh)


@app.command()
def build():
    """Construct labels, run exclusion audits, write base rates and figures."""
    from .build import run

    run()


@app.command()
def backtest():
    """Walk-forward backtest; writes metrics and figures."""
    raise typer.Exit(_pending("backtest"))


@app.command()
def scorecards():
    """Regenerate signal graduation scorecards from the registry."""
    raise typer.Exit(_pending("scorecards"))


@app.command()
def score(quarter: str = typer.Option(..., help="Quarter to score, e.g. 2026Q1")):
    """Emit the ranked alert list as JSON + CSV."""
    raise typer.Exit(_pending("score"))


def _pending(name: str) -> int:
    typer.echo(f"`{name}` is not implemented yet - {_PENDING[name]}. Run `pull` then `build`.")
    return 1


if __name__ == "__main__":
    app()
