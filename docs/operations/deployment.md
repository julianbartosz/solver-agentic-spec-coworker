# Deployment

> **Last updated**: December 2025
>
> **Review triggers**:
> - `docker-compose.yml`, `docker-compose.prod.yml`
> - `Dockerfile`
> - `scripts/setup_env.sh`
> - CLI entrypoints in `src/integration_coworker/cli.py`

This page describes the supported deployment path for Integration Co-Worker:

- Local or single-host deployment via Docker Compose

## 1) Local deployment (Docker Compose)

This is the recommended default distribution model for v1:

- User clones the repo
- Runs a local stack with Postgres, Redis, and the app

Use the repo compose files directly:

- `docker-compose.yml` (dev-ish)
- `docker-compose.prod.yml` (prod-ish)

## 2) Production deployment (Docker Compose)

High-level steps:

1. Clone the repo to the host
2. Configure environment variables (API keys)
3. Bring up the stack with `docker-compose.prod.yml`
4. Initialize the database
5. Run a smoke/health check

### Commands

```bash
# Start the prod-ish stack
docker-compose -f docker-compose.prod.yml up -d

# Initialize/verify schema
python scripts/init_db_postgres.py --verify

# Health check
python -m integration_coworker.cli health
```

### Environment variables

You’ll typically need API keys:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `LANGCHAIN_API_KEY` (optional)

## Notes

A local developer tool is a good v1 default because it has:

- low friction
- no cloud cost
- local filesystem access (important for code generation)

Cloud deployment can be built on the same primitives (Postgres+pgvector, Redis for caching), but this repo’s published docs focus on the Compose-based path.
