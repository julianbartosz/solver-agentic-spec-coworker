# How to add a new file-guide source

This guide is **docs-only** and describes how to add a new **FILE** `SpecSource` implementation (e.g., EDI, JSON Lines, custom “data transmission guide” formats) to the unified routing pipeline.

## 1) Where sources live

- Source implementations live in: `src/integration_coworker/sources/`
  - Examples: `csv_source.py`, `fixed_width.py`, `pdf_guide.py`, `openapi.py`.
- Core contracts live in: `src/integration_coworker/sources/base.py`
  - `SpecSource` protocol (`detect`, `parse`)
  - `ParsedSpec` result object
  - `SourceType` enum (`API`, `FILE`, …)

## 2) Minimum interface (what you must implement)

All sources implement the `SpecSource` protocol (see `src/integration_coworker/sources/base.py`).

### `detect(content, uri, content_type) -> float`

- Purpose: a **fast** heuristic scoring function (0.0–1.0) used for routing.
- Return values:
  - `0.0` means “cannot handle” (hard no)
  - `0.9+` means “very confident match”
- Guidance:
  - Keep it cheap: avoid full parsing.
  - Prefer multiple weak signals (extension, magic bytes, delimiter patterns, etc.).

### `parse(content, uri) -> ParsedSpec`

- Purpose: do full parsing and return a unified output structure.
- Must return a `ParsedSpec` whose:
  - `source_type == SourceType.FILE`
  - `data` is populated for success, and `errors` is populated for failure.

## 3) Explicit hard rejects to avoid collisions with OpenAPI

Because all sources are in one registry and compete via `detect()`, every new file-guide source should include conservative early rejects so it doesn’t steal API specs.

Common patterns:
- If the input looks like OpenAPI/Swagger (`openapi:` / `swagger:` keys), return `0.0`.
- If the URI extension is a strong signal for another format, return `0.0`.

Why: routing picks the best score, and breaks ties by priority; the goal is to make “wrong format” score **0** whenever practical.

## 4) Registration and priority

Sources are registered in `src/integration_coworker/sources/__init__.py`.

Key entrypoints:
- `ensure_sources_registered()` lazily calls `_register_default_sources()`.
- Default priorities are assigned in `_register_default_sources()`.

Rule of thumb:
- Keep your file-guide source priority **below** `OpenAPISource` (priority `90`) unless you’re intentionally overriding OpenAPI routing.

## 5) `ParsedSpec.data` contract for FILE sources (required by the Silver builder)

`build_silver_file_model` consumes **FILE** parsed specs and expects `ParsedSpec.data` to be a **dict**.

At minimum, for a “standard” file spec source:
- `data["file_spec"]` must be a `FileSpec`
- `data["fields"]` must be a list of `FileField`

If either of these are missing or typed incorrectly, the file-spec will be skipped in `build_silver_file_model`.

## 6) Optional dependency handling (ImportError → ParsedSpec.errors)

Several existing sources treat parsing dependencies as optional and convert missing deps into non-fatal `ParsedSpec.errors`.

Recommended behavior:
- In `detect()`: if a required optional parser isn’t installed, return `0.0`.
- In `parse()`: if missing dependencies prevent parsing, return `ParsedSpec(..., data=None, errors=[...])`.

This keeps the workflow resilient even when optional parsers aren’t installed.

## 7) Tests to add (concrete patterns)

Use existing tests as patterns (notably `tests/test_fixed_width_adversarial.py`):

1) **Routing precedence (OpenAPI vs your source)**
- Feed OpenAPI-like YAML/JSON and assert your source’s `detect()` returns `0.0`.

2) **“detect returns 0 for OpenAPI-like inputs”**
- A unit-level test against your source’s `detect()`.

3) **Parse happy path**
- Call `parse()` and assert:
  - `ParsedSpec.source_type == SourceType.FILE`
  - `ParsedSpec.is_valid()` is true
  - `ParsedSpec.data` is a dict and contains `file_spec` + `fields`

4) **Warnings/errors behavior**
- If your format supports “confidence gating”, add a contract-style test similar to `TestConfidenceGatesContract` to prove gates aren’t accidentally removed.

## 8) Minimal dev checklist (before opening a PR)

- [ ] Implement `detect()` with conservative hard rejects.
- [ ] Implement `parse()` returning `ParsedSpec(source_type=FILE, data={...})`.
- [ ] Ensure `ParsedSpec.data` is a dict with `file_spec` and `fields`.
- [ ] Handle missing optional deps gracefully (ImportError → `errors`, `data=None`).
- [ ] Register the new source in `_register_default_sources()` with `< OpenAPISource` priority unless explicitly justified.
- [ ] Add **SQLite-safe** tests for routing/detect/parse.
- [ ] If adding KG/vector behavior, add **Postgres-only** tests and label them clearly.
