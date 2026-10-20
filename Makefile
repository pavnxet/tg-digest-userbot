# Simple wrappers. Nothing here is required to run the bot — Termux users can
# just use the `python ...` commands directly.
.PHONY: help install dev test test-all lint format audit run bot clean

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:         ## Install runtime dependencies
	python -m pip install -r requirements.txt

dev:             ## Install runtime + dev dependencies
	python -m pip install -r requirements.txt pytest pytest-cov hypothesis jsonschema ruff mypy bandit

test:            ## Run the test suite (fast: skips the 100s perf file)
	python -m pytest -q --ignore=tests/test_phase7_performance.py

test-all:        ## Run every test, including the slow performance file
	python -m pytest -q

lint:            ## ruff + mypy + bandit
	ruff check .
	mypy
	bandit -q -r . -x ./tests

format:          ## Auto-format and auto-fix
	ruff format .
	ruff check --fix .

audit:           ## Check dependencies for known vulnerabilities
	pip-audit

run:             ## Run the scraper for today (dry run)
	python main.py --today --dry-run

bot:             ## Start the control bot
	python bot.py

clean:           ## Remove caches and build artefacts (keeps output/ and .cache/)
	rm -rf .pytest_cache .mypy_cache .ruff_cache __pycache__ */__pycache__ build dist *.egg-info
