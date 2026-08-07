PY := .venv/bin/python
CLI := $(PY) -m drawdown_radar.cli

.PHONY: help setup pull build backtest scorecards score test lint all

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup:  ## create the venv and install the package with dev extras
	uv venv --python 3.12
	uv pip install -e ".[dev]"

pull:  ## fetch + cache FDIC data (idempotent; re-runs hit the parquet cache)
	$(CLI) pull

build:  ## labels, exclusion audit, base rates, figures
	$(CLI) build

backtest:  ## walk-forward backtest
	$(CLI) backtest

scorecards:  ## regenerate signal graduation scorecards
	$(CLI) scorecards

score:  ## ranked alert list for QUARTER=2026Q1
	$(CLI) score --quarter $(or $(QUARTER),2026Q1)

test:  ## run the test suite
	$(PY) -m pytest -q

lint:  ## ruff check + format check
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

all: pull build test  ## full pipeline from a cold start
