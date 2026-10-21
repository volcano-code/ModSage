PYTHON ?= .venv/bin/python

.PHONY: setup api web worker worker-once recommendation-worker recommendation-worker-once jobs enqueue-refresh embed embedding-status evaluate test lint build init-db discover discover-public digest

setup:
	python3 -m venv .venv
	$(PYTHON) -m pip install --require-hashes -r apps/api/requirements-dev.lock
	$(PYTHON) -m pip install --no-deps -e 'apps/api[dev]'
	npm --prefix apps/web ci

init-db:
	$(PYTHON) -m modsage.cli init-db

api:
	$(PYTHON) -m uvicorn modsage.main:app --host 127.0.0.1 --port 8000

web:
	npm --prefix apps/web run dev

worker:
	$(PYTHON) -m modsage.cli worker

worker-once:
	$(PYTHON) -m modsage.cli worker --once

recommendation-worker:
	$(PYTHON) -m modsage.cli recommendation-worker

recommendation-worker-once:
	$(PYTHON) -m modsage.cli recommendation-worker --once

jobs:
	$(PYTHON) -m modsage.cli jobs --limit 20

enqueue-refresh:
	$(PYTHON) -m modsage.cli enqueue-refresh

discover:
	$(PYTHON) -m modsage.cli discover --limit 50

discover-public:
	$(PYTHON) -m modsage.cli discover --public --limit 50

digest:
	$(PYTHON) -m modsage.cli digest

embed:
	$(PYTHON) -m modsage.cli embed --limit 20 --batch-size 8

embedding-status:
	$(PYTHON) -m modsage.cli embedding-status

evaluate:
	$(PYTHON) scripts/evaluate_retrieval.py

test:
	$(PYTHON) -m pytest apps/api/tests

lint:
	$(PYTHON) -m ruff check apps/api scripts
	$(PYTHON) -m ruff format --check apps/api scripts
	npm --prefix apps/web run lint
	npm --prefix apps/web run typecheck

build:
	npm --prefix apps/web run build
