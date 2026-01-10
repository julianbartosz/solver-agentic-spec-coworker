# Docs tooling

This repo treats documentation as **buildable software**.

The canonical site config is `mkdocs.yml`, and CI enforces a strict build (`mkdocs build --strict`).

## Install docs dependencies

Docs dependencies are intentionally **separated from runtime** dependencies.

Install the package in editable mode with the `docs` extra:

```bash
python -m pip install -e ".[docs]"
```

If you use the managed environment created via `scripts/setup_env.sh`, make sure you’re working inside `.venv311`.

## Local verification (matches CI)

Run the same three docs gates CI runs:

```bash
make docs-verify
```

This runs (stop-on-first-failure):

1. `mkdocs build --strict`
2. `python -m pytest tests/test_mkdocs.py -q`
3. `python scripts/docs_audit.py`

## Docs plugins

Custom MkDocs plugins live in Python package code under:

- `src/integration_coworker/docs_plugins/`

They are loaded by MkDocs via the entry point defined in `pyproject.toml`:

- `[project.entry-points."mkdocs.plugins"]`

This keeps docs behavior versioned and importable (no filesystem hacks).
