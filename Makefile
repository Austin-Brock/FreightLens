# FreightLens developer tasks. Everything runs out of backend/ against the
# local .venv, so `make install` once and the rest work from a clean checkout.
.DEFAULT_GOAL := help
.PHONY: help install dev test lint migrate revision reset seed audit clean

VENV    := backend/.venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
BACKEND := cd backend &&

help: ## List the available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install dev dependencies
	python3 -m venv $(VENV)
	$(PIP) install -q -r backend/requirements-dev.txt
	@echo "Installed. Next: make dev"

dev: ## Run the API and dashboard on http://localhost:8000 with reload
	$(BACKEND) ../$(VENV)/bin/uvicorn app.main:app --reload

test: ## Run the test suite
	$(BACKEND) ../$(VENV)/bin/pytest

lint: ## Lint with ruff
	$(BACKEND) ../$(VENV)/bin/ruff check .

migrate: ## Apply migrations to the configured database
	$(BACKEND) ../$(VENV)/bin/alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add carrier table"
	@test -n "$(m)" || (echo 'usage: make revision m="what changed"'; exit 1)
	$(BACKEND) ../$(VENV)/bin/alembic revision --autogenerate -m "$(m)"

seed: ## Load sample_data/ into the database and audit it
	$(BACKEND) ../$(PY) -c "from app.db.init_db import init_db; init_db(seed=True)"

audit: ## Re-run the audit over every invoice and print the totals
	$(BACKEND) ../$(PY) -c "\
from app.db.session import SessionLocal; \
from app.services.audit_engine import run_audit; \
db = SessionLocal(); run = run_audit(db); \
print(f'{run.exceptions_found} exception(s) across {run.invoices_audited} invoice(s); {run.total_variance} recoverable')"

reset: ## Delete the local database so the next start re-seeds from scratch
	rm -f backend/freightlens.db
	@echo "Database removed; it is recreated and seeded on the next start."

clean: reset ## reset, plus caches and bytecode
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.ruff_cache
