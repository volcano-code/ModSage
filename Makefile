PYTHON ?= .venv/bin/python

.PHONY: setup api web test lint build init-db discover discover-public digest

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

discover:
	$(PYTHON) -m modsage.cli discover --limit 50

discover-public:
	$(PYTHON) -m modsage.cli discover --public --limit 50

digest:
	$(PYTHON) -m modsage.cli digest

test:
	$(PYTHON) -m pytest apps/api/tests

lint:
	$(PYTHON) -m ruff check apps/api scripts
	$(PYTHON) -m ruff format --check apps/api scripts
	npm --prefix apps/web run lint
	npm --prefix apps/web run typecheck

build:
	npm --prefix apps/web run build
