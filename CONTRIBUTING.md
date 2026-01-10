# Contributing to Integration Co-Worker

Thank you for your interest in contributing!

## Quick Links

- **[Full Contributing Guide](docs/development/contributing.md)** — Development setup, code style, PR process
- **[Code Tour](docs/development/code-tour.md)** — 60-90 minute codebase walkthrough
- **[Architecture](docs/development/architecture.md)** — System design and data flow
- **[First Week Checklist](docs/operations/FIRST_WEEK.md)** — New maintainer onboarding

## TL;DR

```bash
# Setup
./scripts/setup_env.sh
source .venv311/bin/activate

# Test
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format src/ tests/

# Docs
mkdocs serve
```

## Submitting Changes

1. Fork and create a feature branch
2. Make changes with tests
3. Ensure CI passes: `pytest`, `ruff check`, `mkdocs build --strict`
4. Submit PR with clear description

See the [full guide](docs/development/contributing.md) for details.
