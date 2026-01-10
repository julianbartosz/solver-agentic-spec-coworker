# Database: Postgres + pgvector

> Source material consolidated from the legacy `db_setup_postgres.md` guide.
>
> **Last updated**: December 2025
>
> **Review triggers**:
> - `docker-compose.yml`, `docker-compose.prod.yml`
> - `scripts/init_db_postgres.py`, `scripts/init-pgvector.sql`
> - `src/integration_coworker/persistence/{db,postgres}.py` (schema + vector index DDL)

## Overview

The Integration Co-Worker supports two database backends:

| Backend | Use Case | Vector Search |
|---------|----------|---------------|
| **SQLite** | Local dev, testing, single-user | Python cosine similarity |
| **Postgres + pgvector** | Production, team deployment | Native vector operators (`<=>`) |

## Prerequisites

- PostgreSQL 16 (recommended)
- pgvector-enabled Postgres (recommended: `pgvector/pgvector:pg16`)
- Python 3.11.x with Postgres extras (`psycopg[binary]`, `psycopg_pool`)

## Quick start (recommended: Docker Compose)

This repo ships a Postgres+pgvector service definition.

1) Start the database:

```bash
docker-compose up -d db
```

2) Configure the app to use Postgres:

```bash
export DATABASE_URL="postgresql://integration:integration@localhost:5432/integration_coworker"
```

3) Initialize schema:

```bash
python scripts/init_db_postgres.py
python scripts/init_db_postgres.py --verify
```

## Configuration

### Environment variables

```bash
export DATABASE_URL="postgresql://integration:integration@localhost:5432/integration_coworker"
```

### Python dependencies

If Postgres dependencies are missing, install the optional extras:

```bash
pip install -e ".[postgres]"
```

## Operations: vector search tuning

For large datasets, tune ivfflat parameters:

```sql
-- Example only: tune based on dataset size and latency targets.
-- The exact index name depends on the version of the schema.
SET ivfflat.probes = 10;
```

## Troubleshooting

- **pgvector extension not found**
- **connection refused**
- **permission denied for schema**
- **vector dimension mismatch (expected 1536)**

See the original source page for expanded examples.
