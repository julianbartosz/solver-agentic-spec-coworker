# Database Migrations

This directory contains SQL migration files for schema evolution.

## Structure

- `001_baseline_v1.sql` - Initial schema (spec_silver, spec_bronze, integration_gold, repo_meta, kg)
- Future migrations follow pattern: `NNN_description.sql`

## How It Works

1. Migrations are numbered SQL files: `001_`, `002_`, etc.
2. Each migration runs in its own transaction
3. `schema_migrations` table tracks applied versions
4. `run_pending_migrations()` applies unapplied migrations in order
5. On failure, the failing migration is rolled back and process stops

## Usage

Migrations run automatically during `init_postgres_schema()`.

To check migration status:
```python
from integration_coworker.persistence.migrations import get_migration_status
status = get_migration_status()
```

## Writing Migrations

1. Create file: `migrations/NNN_description.sql` (NNN = next number)
2. Use `IF NOT EXISTS` for idempotency where possible
3. Use `DO $$ BEGIN ... END $$;` blocks for conditional DDL
4. Test on PostgreSQL (not just SQLite) - some features are Postgres-only

## Notes

- All DDL uses raw SQL (no SQLAlchemy ORM)
- SQLite schema in `db.py` is separate (for tests only)
- Vector indexes require pgvector extension
