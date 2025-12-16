# Validation

> **Last updated**: December 2025
>
> **Review triggers**:
> - `Makefile` target `docs-verify`
> - `tests/test_mkdocs.py`
> - CLI commands in `src/integration_coworker/cli.py` (`health`, `status`, `demo`)
> - Postgres schema init scripts: `scripts/init_db_postgres.py`, `scripts/init-pgvector.sql`

This page is the operator-focused checklist for validating that the docs, CLI surface, and basic system posture are consistent.

## 1) Docs / site health (strict mode)

Run the docs gates exactly as CI expects:

```bash
make docs-verify
```

This runs (stop-on-first-failure):

1. `mkdocs build --strict`
2. `python -m pytest tests/test_mkdocs.py -q`
3. `python scripts/docs_audit.py`

## 2) Database readiness

### Postgres + pgvector

If you’re running the Compose database (`docker-compose.yml` / `docker-compose.prod.yml`):

```bash
python scripts/init_db_postgres.py --verify
python -m integration_coworker.cli status
```

## 3) Runtime health

```bash
python -m integration_coworker.cli health
```

If you want LLM connectivity included (requires API keys):

```bash
python -m integration_coworker.cli health --verbose --check-llm
```
