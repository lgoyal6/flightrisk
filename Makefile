PY := .venv/bin/python
CLI := $(PY) -m flightrisk.cli

.PHONY: help setup pull build backtest falsify text scorecards score test lint all

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup:  ## create the venv and install the package with dev extras
	uv venv --python 3.12
	uv pip install -e ".[dev]"

pull:  ## fetch + cache FDIC data (idempotent; re-runs hit the parquet cache)
	$(CLI) pull

build:  ## labels, exclusion audit, base rates, figures
	$(CLI) build

backtest:  ## walk-forward backtest, leakage checks, out-of-time, ablations
	$(CLI) backtest

falsify:  ## the two falsification experiments (recover-the-logit, unseen-entity)
	$(CLI) falsify

# Deliberately NOT part of `all`: this fetches ~836 documents from SEC EDGAR and makes ~836
# LLM calls. Both are cached, so a re-run is free, but a first run is not -- `all` should never
# silently spend network and model budget. Run it explicitly.
text:  ## SEC 8-K extraction + incremental-lift test (NOT in `all`: hits EDGAR + an LLM)
	$(CLI) text

scorecards:  ## regenerate signal graduation scorecards
	$(CLI) scorecards

score:  ## ranked alert list for QUARTER=2026Q1
	$(CLI) score --quarter $(or $(QUARTER),2026Q1)

page-data:  ## regenerate the JSON behind docs/ (needs `build` and `backtest` first)
	$(PY) scripts/make_page_data.py

test:  ## run the test suite
	$(PY) -m pytest -q

lint:  ## ruff check + format check
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

all: pull build backtest falsify scorecards score test  ## full pipeline (excludes `text`; see above)
