# Database Setup Guide

This document explains how to set up the Postgres + pgvector database for the Integration Co-Worker.

## Overview

The Integration Co-Worker uses:
- **PostgreSQL 15+** as the primary database
- **pgvector** extension for 1536-dimensional embeddings (for RAG)
- **SQLite** as a fallback for tests only (set `USE_SQLITE=true`)

## Quick Start with Docker

### Option 1: Single Docker Command

Run a pgvector-enabled Postgres container:

```bash
docker run -d \
  --name integration-coworker-db \
  -e POSTGRES_USER=integration \
  -e POSTGRES_PASSWORD=integration \
  -e POSTGRES_DB=integration_coworker \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

Then enable the vector extension:

```bash
docker exec -it integration-coworker-db psql -U integration -d integration_coworker -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### Option 2: Docker Compose

Create a `docker-compose.yml` in the project root:

```yaml
version: '3.8'

services:
  db:
    image: pgvector/pgvector:pg16
    container_name: integration-coworker-db
    environment:
      POSTGRES_USER: integration
      POSTGRES_PASSWORD: integration
      POSTGRES_DB: integration_coworker
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./scripts/init-pgvector.sql:/docker-entrypoint-initdb.d/init-pgvector.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U integration -d integration_coworker"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

Create `scripts/init-pgvector.sql`:

```sql
-- Enable pgvector extension for embeddings
CREATE EXTENSION IF NOT EXISTS vector;
```

Then run:

```bash
docker-compose up -d
```

## Environment Variables

Set these in your `.env` file or environment:

```bash
# Required for Postgres mode
DATABASE_URL="postgresql://integration:integration@localhost:5432/integration_coworker"

# Optional: Force SQLite for tests (disables Postgres)
# USE_SQLITE=true

# Optional: Custom SQLite path
# SQLITE_PATH=/path/to/custom.sqlite3
```

## Initializing the Schema

After Postgres is running, initialize the database schema:

```bash
# Using the CLI command
PYTHONPATH=src python -m integration_coworker.cli init-db

# Or using the helper script
PYTHONPATH=src python scripts/init_db.py
```

This creates the following schemas:
- `spec_silver` - API specification metadata (endpoints, schemas, embeddings)
- `integration_gold` - Integration task data (workflows, code artifacts)
- `repo_meta` - Repository tracking data

## Verifying the Setup

Check database connectivity and pgvector:

```bash
# Check status
PYTHONPATH=src python -m integration_coworker.cli status

# Or check manually
docker exec -it integration-coworker-db psql -U integration -d integration_coworker -c "\dx"
```

Expected output should show the `vector` extension:

```
                    List of installed extensions
  Name   | Version |   Schema   |         Description
---------+---------+------------+------------------------------
 plpgsql | 1.0     | pg_catalog | PL/pgSQL procedural language
 vector  | 0.7.0   | public     | vector data type
```

## Connection Pooling

The application uses `psycopg_pool` for connection pooling with these defaults:
- **min_size**: 1 connection
- **max_size**: 10 connections

## Troubleshooting

### "Postgres is configured but required dependencies are missing"

Install the Postgres client libraries:

```bash
pip install 'psycopg[binary]' psycopg_pool
```

### "Failed to connect to Postgres database"

1. Check Postgres is running: `docker ps | grep integration-coworker-db`
2. Verify connection: `docker exec -it integration-coworker-db pg_isready -U integration`
3. Check DATABASE_URL is set correctly
4. Ensure port 5432 is not blocked

### "pgvector extension not found"

Ensure you're using the `pgvector/pgvector` Docker image, not plain `postgres`.

### Using with existing Postgres

If you have an existing Postgres 15+ installation:

1. Install pgvector extension (see [pgvector docs](https://github.com/pgvector/pgvector))
2. Create the database: `createdb integration_coworker`
3. Enable extension: `psql -d integration_coworker -c "CREATE EXTENSION IF NOT EXISTS vector;"`
4. Set DATABASE_URL to your connection string

## Test Mode (SQLite)

For running tests without Postgres:

```bash
USE_SQLITE=true PYTHONPATH=src python -m pytest tests/ -v
```

This uses a local SQLite file at `.data/integration_coworker.sqlite3`.

**Note**: SQLite mode does not support pgvector embeddings. Vector columns are stored as JSON text.
