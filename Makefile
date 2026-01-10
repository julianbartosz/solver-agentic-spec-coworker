# Makefile for Integration Co-Worker
# 
# Common development tasks and test targets

.PHONY: help install test test-unit test-postgres test-all lint clean db-up db-down
.PHONY: coverage coverage-generated security

# Default target
help:
	@echo "Available targets:"
	@echo "  install       - Install dependencies"
	@echo "  test          - Run unit tests (SQLite)"
	@echo "  test-unit     - Alias for 'test'"
	@echo "  test-postgres - Run Postgres integration tests"
	@echo "  test-all      - Run all tests (requires Postgres)"
	@echo "  lint          - Run linter"
	@echo "  clean         - Remove build artifacts"
	@echo "  db-up         - Start Postgres via docker-compose"
	@echo "  db-down       - Stop Postgres"

# =============================================================================
# Installation
# =============================================================================

install:
	pip install -e ".[all]"

install-dev:
	pip install -e ".[dev,postgres]"

# =============================================================================
# Testing
# =============================================================================

# Unit tests with SQLite (no external dependencies)
test:
	USE_SQLITE=true pytest tests/ -v --ignore=tests/test_pattern_learning_postgres.py -m "not postgres"

test-unit: test

# Pattern learning tests (SQLite)
test-patterns:
	USE_SQLITE=true pytest tests/test_pattern_learning.py tests/test_align_task_with_kg.py -v

# Postgres integration tests (requires docker-compose db)
# Sets POSTGRES_TESTS_REQUIRED=true so tests FAIL (not skip) if Postgres unavailable
test-postgres: db-wait
	POSTGRES_TESTS_REQUIRED=true \
	DATABASE_URL=postgresql://integration:integration@localhost:5432/integration_coworker \
	pytest tests/test_pattern_learning_postgres.py -v -m postgres

# All tests (requires Postgres running)
test-all: db-wait
	POSTGRES_TESTS_REQUIRED=true \
	DATABASE_URL=postgresql://integration:integration@localhost:5432/integration_coworker \
	pytest tests/ -v

# Full CI suite (starts Postgres, runs tests, stops Postgres)
test-ci:
	$(MAKE) db-up
	$(MAKE) db-wait
	$(MAKE) test-all || ($(MAKE) db-down && exit 1)
	$(MAKE) db-down

# =============================================================================
# Database Management
# =============================================================================

db-up:
	docker-compose up -d db
	@echo "Postgres starting..."

db-down:
	docker-compose down

db-wait:
	@echo "Waiting for Postgres to be ready..."
	@timeout=30; \
	while ! docker-compose exec -T db pg_isready -U integration -d integration_coworker >/dev/null 2>&1; do \
		timeout=$$((timeout - 1)); \
		if [ $$timeout -le 0 ]; then \
			echo "Timeout waiting for Postgres"; \
			exit 1; \
		fi; \
		sleep 1; \
	done
	@echo "Postgres is ready"

db-init:
	python scripts/init_db_postgres.py

db-shell:
	docker-compose exec db psql -U integration -d integration_coworker

# =============================================================================
# Linting & Formatting
# =============================================================================

lint:
	ruff check src/ tests/

lint-fix:
	ruff check src/ tests/ --fix

format:
	ruff format src/ tests/

# =============================================================================
# Coverage & Security
# =============================================================================

coverage:
	USE_SQLITE=true VALIDATION_PROFILE=offline \
	pytest tests/ -v --cov=src --cov-report=term-missing --cov-report=json:coverage.json --cov-fail-under=60
	python scripts/check_generated_coverage.py --coverage-json coverage.json --prefix packages/integration-coworker-runtime --fail-under 60 --require-matches

coverage-generated:
	@echo "Run coverage first to produce coverage.json, then validate generated modules"
	python scripts/check_generated_coverage.py --coverage-json coverage.json --prefix packages/integration-coworker-runtime --fail-under 60 --require-matches

coverage-generated-ci:
	python -m coverage json -o coverage.json
	python scripts/check_generated_coverage.py --coverage-json coverage.json --prefix packages/integration-coworker-runtime --fail-under 60 --require-matches

security:
	bandit -c pyproject.toml -r src tests

# =============================================================================
# Cleanup
# =============================================================================

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# =============================================================================
# Documentation
# =============================================================================

docs-serve:
	mkdocs serve

docs-build:
	mkdocs build

# Docs quality gates (matches CI order). Uses the project's .venv311 env.
docs-verify:
	@if [ ! -x ".venv311/bin/python" ]; then echo "Missing .venv311. Run scripts/setup_env.sh"; exit 1; fi
	@.venv311/bin/mkdocs build --strict
	@.venv311/bin/python -m pytest tests/test_mkdocs.py -q
	@.venv311/bin/python scripts/docs_audit.py

# =============================================================================
# Development Helpers
# =============================================================================

# Run a single test file
test-file:
	@if [ -z "$(FILE)" ]; then echo "Usage: make test-file FILE=tests/test_foo.py"; exit 1; fi
	USE_SQLITE=true pytest $(FILE) -v

# Run tests matching a pattern
test-pattern:
	@if [ -z "$(PATTERN)" ]; then echo "Usage: make test-pattern PATTERN=test_crud"; exit 1; fi
	USE_SQLITE=true pytest tests/ -v -k "$(PATTERN)"
