# Installation

## Requirements

- Python 3.11+
- PostgreSQL 14+ with pgvector (recommended) or SQLite
- OpenAI API key (for embeddings)
- Anthropic API key (for planning and codegen)

## Install from Source

```bash
# Clone the repository
git clone https://github.com/julianbartosz/solver-agentic-spec-coworker.git
cd solver-agentic-spec-coworker

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dependencies
pip install -e ".[postgres]"
```

## Install with Extras

```bash
# Full installation with UI and all features
pip install -e ".[all]"

# Development installation
pip install -e ".[dev]"

# Just the UI
pip install -e ".[ui]"

# LLM caching support
pip install -e ".[cache]"

# Documentation tools
pip install -e ".[docs]"
```

## Docker Setup

The easiest way to get started with PostgreSQL:

```bash
docker-compose up -d postgres
integration-coworker init-db
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | Required |
| `ANTHROPIC_API_KEY` | Anthropic API key | Required for LLM |
| `DATABASE_URL` | PostgreSQL connection | `postgresql://localhost/integration` |
| `USE_SQLITE` | Use SQLite instead | `false` |
| `USE_MOCK_LLM` | Mock LLM for testing | `false` |
| `REDIS_URL` | Redis URL for caching | `redis://localhost:6379` |
| `LLM_CACHE_ENABLED` | Enable LLM response cache | `false` |
| `PARALLEL_WORKFLOW` | Enable parallel execution | `false` |

## Verify Installation

```bash
# Check CLI is available
integration-coworker --help

# Run health check
integration-coworker health

# Run demo in mock mode
USE_SQLITE=true USE_MOCK_LLM=true integration-coworker demo
```

---

[Next: Quickstart →](quickstart.md)
