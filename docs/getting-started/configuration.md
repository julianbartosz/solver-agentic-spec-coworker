# Configuration

This guide covers all configuration options for Integration Co-Worker.

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key for embeddings | `sk-...` |
| `ANTHROPIC_API_KEY` | Anthropic API key for planning/codegen | `sk-ant-...` |

### Database

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://localhost/integration` |
| `USE_SQLITE` | Use SQLite instead of PostgreSQL | `false` |

### LLM Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `USE_MOCK_LLM` | Use mock LLM for testing | `false` |
| `LLM_CACHE_ENABLED` | Enable Redis LLM response cache | `false` |
| `LLM_CACHE_TTL` | Cache TTL in seconds | `86400` (24 hours) |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379` |

### Workflow

| Variable | Description | Default |
|----------|-------------|---------|
| `PARALLEL_WORKFLOW` | Enable parallel node execution | `false` |

### Observability

| Variable | Description | Default |
|----------|-------------|---------|
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing | `false` |
| `LANGCHAIN_API_KEY` | LangSmith API key | - |
| `LANGCHAIN_PROJECT` | LangSmith project name | `integration-coworker` |

## Database Setup

### SQLite (Development)

SQLite is the default for local development:

```bash
export USE_SQLITE=true
# Data stored at .data/integration_coworker.sqlite3
```

### PostgreSQL with pgvector (Production)

```bash
# Using Docker
docker run -d \
  --name integration-coworker-db \
  -e POSTGRES_USER=integration \
  -e POSTGRES_PASSWORD=integration \
  -e POSTGRES_DB=integration_coworker \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# Enable vector extension
docker exec -it integration-coworker-db psql -U integration -d integration_coworker \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Set connection string
export DATABASE_URL="postgresql://integration:integration@localhost:5432/integration_coworker"

# Initialize schema
python -m integration_coworker.cli init-db
```

## LLM Configuration

The system uses multiple LLM providers:

| Purpose | Provider | Model |
|---------|----------|-------|
| Task understanding & planning | Anthropic | claude-sonnet-4 |
| Code generation | Anthropic | claude-sonnet-4 |
| Report summarization | OpenAI | gpt-4o-mini |
| Embeddings | OpenAI | text-embedding-3-small |

### Enabling LLM Caching

To reduce API costs and latency:

```bash
# Start Redis
docker-compose up -d redis

# Enable caching
export LLM_CACHE_ENABLED=true
export REDIS_URL="redis://localhost:6379"

# Optional: Customize TTL (default 24 hours)
export LLM_CACHE_TTL=43200  # 12 hours
```

Monitor cache performance:

```bash
integration-coworker cache-stats
```

## Model Configuration

Models are configured in `config/models.yaml`:

```yaml
llm:
  planning:
    model: gpt-4.1
    temperature: 0.2
    max_tokens: 2048

  extraction:
    model: gpt-4o-mini
    temperature: 0.0
    max_tokens: 1024

  codegen:
    model: gpt-4.1
    temperature: 0.15
    max_tokens: 4096

embeddings:
  model: text-embedding-3-small
  dimensions: 1536
```

## Troubleshooting

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Nodes fall back to heuristics" | Missing API key | Set `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` |
| "Failed to connect to Postgres" | DB not running | `docker-compose up -d postgres` |
| "Vector dimension mismatch" | Wrong embedding model | Ensure using `text-embedding-3-small` (1536 dims) |
| "Cache connection failed" | Redis not running | `docker-compose up -d redis` |

### Health Check

Run comprehensive health check:

```bash
python -m integration_coworker.cli health --verbose --check-llm
```

---

[Back to Quickstart](quickstart.md) | [CLI Reference →](../user-guide/cli-reference.md)
