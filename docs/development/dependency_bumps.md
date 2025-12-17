# Dependency bump playbook (docs tooling)

This project intentionally runs MkDocs in **strict mode**, which aborts on warnings.

That makes MkDocs an ideal quality gate, but it also means dependency bumps must be handled carefully.

## Before you bump

1. Ensure docs deps are installed:

   ```bash
   python -m pip install -e ".[docs]"
   ```

2. Start from a clean baseline:

   ```bash
   make docs-verify
   ```

## Bump procedure

When bumping any of these, treat it as a coupled upgrade:

- `mkdocs`
- `mkdocs-material`
- `mkdocs-autorefs`
- `mkdocstrings`
- `mkdocstrings-python`
- `griffe`

Recommended sequence:

1. **Change pins in one place**: `pyproject.toml` under `[project.optional-dependencies].docs`.
2. Reinstall:

   ```bash
   python -m pip install -e ".[docs]"
   ```

3. Run the strict gate first:

   ```bash
   mkdocs build --strict
   ```

4. If it passes, run the full docs gate trio:

   ```bash
   make docs-verify
   ```

## Handling strict-mode failures

### Broken links / missing anchors

Strict builds abort on warnings, so fix these in-place:

- Update broken links to their canonical location
- Fix internal anchors to match actual heading slug generation
- Prefer adding explicit anchors when needed

Avoid “solving” this by turning off strict mode.

### Autorefs / cross-reference warnings

If a string like `<METHOD>` or `"task_id"` is interpreted as a cross-reference target:

- Wrap it in inline code: `` `<METHOD>` ``
- Or rephrase the sentence so it’s not parsed as a reference target

### mkdocstrings / griffe compatibility

mkdocstrings-python depends on Griffe internals that have changed across major versions.

If you see import errors like missing `griffe.collections`, pin Griffe below 1.0:

- `griffe>=0.37,<1.0`

(That pin is currently part of the `docs` extra.)

## Invariants we protect

CI also enforces a key navigation invariant:

- `GETTING_STARTED.md` must remain visible in `mkdocs.yml` under **Ops Runbook**
- It must not be redirected away via `mkdocs-redirects`

If you move content, update `mkdocs.yml` and the invariant test in `tests/test_mkdocs.py`.
