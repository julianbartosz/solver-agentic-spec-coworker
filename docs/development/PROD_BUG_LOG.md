# Production Bug Log

> **Safety**: This log must never include secrets. Redact values for tokens/keys/URLs with credentials.
> 
> **Date range**: 2025-12-16 onward

## Environment snapshot (names only; no values)

- OS: macOS
- Python: (fill via runtime command output; don’t paste full `sys.path`)
- Key env vars used (names only):
  - `DATABASE_URL`
  - `USE_SQLITE`
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `LLM_MODE`
  - `LLM_PROVIDER`
  - `LLM_MODEL`
  - `STREAMING_PERSISTENCE`
  - `LANGCHAIN_TRACING_V2` / `LANGSMITH_API_KEY`

---

## Bugs

### BUG-0006 — Parallel workflow crash: INVALID_CONCURRENT_GRAPH_UPDATE on `file_specs`

- **Severity**: blocker
- **Area**: workflow / parallel execution
- **Owner module(s)**: `src/integration_coworker/graph/runtime.py`, `src/integration_coworker/graph/state_v2.py`
- **First seen in**: Validation (production demo)

#### Repro (exact commands)

```bash
export CODEGEN_PROFILE=production
export PARALLEL_WORKFLOW=true
export USE_MOCK_LLM=false

# DATABASE_URL / provider keys required; keep values out of logs
python scripts/production_demo.py --specs httpbin --verbose
```

#### Expected

- Parallel branches should merge cleanly when using `build_parallel_graph()`.

#### Actual

- LangGraph raised `INVALID_CONCURRENT_GRAPH_UPDATE` for key `file_specs`.

#### Evidence

- Parallel graph is used when `PARALLEL_WORKFLOW=true` via `build_parallel_graph()`.
- `WorkflowStateDict` reducers define merge semantics per key.

#### Suspected root cause

- In parallel execution, multiple branches can emit values for the same state key in a single step.
- The dataclass → dict conversion can include default/unchanged fields, causing redundant per-branch updates.

#### Fix options

1) Minimal patch:
  - Ensure `WorkflowStateDict` defines explicit reducer semantics for file-integration fields
  - Avoid emitting default/unchanged values from parallel branches
2) Hybrid:
  - Only return explicit “delta dicts” from nodes (more invasive refactor)
3) Scalable redesign:
  - Move fully to dict-based state and enforce per-node output schemas (bigger change)

#### Chosen direction (if decided)

- Minimal patch applied:
  - `src/integration_coworker/graph/state_v2.py` updated to reduce redundant updates and ensure stable merge semantics.

Verification:

```bash
python scripts/production_demo.py --specs httpbin --verbose
```

Result: `✅ All specs processed successfully!`

---

### BUG-0007 — Production Postgres validation script: “valid code” fixture fails `ruff format --check`

- **Severity**: medium (validation signal broken / flaky)
- **Area**: codegen / quality gates / validation
- **Owner module(s)**: `scripts/test_production_postgres.py`, `src/integration_coworker/codegen/sandbox.py`
- **First seen in**: Validation (production posture script)

#### Repro (exact commands)

```bash
export CODEGEN_PROFILE=production
python scripts/test_production_postgres.py
```

#### Expected

- `strict_gates_valid_code` should pass when the sandbox is properly configured.

#### Actual

- `strict_gates_valid_code` failed with:
  - `[FORMAT] Would reformat: src/processor.py`

#### Suspected root cause

- The test fixture snippet is intended to represent “valid code”, but it was brittle to ruff-format exact output.

#### Fix options

1) Minimal patch:
  - Adjust the fixture content until it passes formatting checks (brittle across ruff versions)
2) Hybrid (chosen):
  - Pre-format the fixture using `ruff format` before running strict gates
3) Scalable redesign:
  - Move fixtures into golden files and pin tool versions inside sandbox (heavier)

#### Chosen direction (if decided)

- Hybrid fix applied in `scripts/test_production_postgres.py`: pre-format sample with ruff, then validate gates.

Verification:

```bash
python scripts/test_production_postgres.py
```

Result: `✅ All production tests passed!` (5/5)

---

### BUG-0008 — KG delta accounting anomaly (negative created counts)

- **Severity**: medium (observability correctness)
- **Area**: reporting / KG metrics
- **Owner module(s)**: (likely `scripts/production_demo.py` and/or persistence/metrics)
- **First seen in**: Validation (production demo)

#### Repro (exact commands)

```bash
python scripts/production_demo.py --specs httpbin --verbose
```

#### Expected

- “KG Nodes Created” and “KG Edges Created” should be >= 0 for a single run.

#### Actual

- Demo results showed negative deltas (example):
  - `KG Nodes Created: -8`
  - `KG Edges Created: -10`

#### Suspected root cause

- “Before/after” KG snapshot may be mixing runs, or counting is not scoped to the current run/template insertion.

#### Fix options

1) Minimal patch:
  - Clamp negative deltas to 0 in presentation (paper-over)
2) Hybrid:
  - Compute deltas based on run-scoped inserts (use run_id/template ids)
3) Scalable redesign:
  - Add a metrics table keyed by run_id and write authoritative counters during persistence

#### Chosen direction (if decided)

- Not decided yet.

### BUG-0009 — Large specs cause checkpoint memory failures and LLM hallucinations

- **Severity**: high
- **Area**: persistence / codegen / llm
- **Owner module(s)**: `src/integration_coworker/persistence/checkpoints.py`, `src/integration_coworker/codegen/prompts.py`, `src/integration_coworker/graph/nodes/generate_code_and_tests.py`
- **First seen in**: Production demo with github_api.json (11MB, 313K lines)

#### Repro (exact commands)

```bash
export CODEGEN_PROFILE=production
export DATABASE_URL='postgresql://...'

# Run with large spec
python -m integration_coworker.cli run \
  --spec-ref specs/github_api.json \
  --task "Create a new issue in a repository" \
  --repo-root /tmp/target
```

#### Expected

- Large specs (>5MB) should be processed without memory errors
- LLM should generate valid API paths that exist in the spec

#### Actual

- PostgreSQL checkpoint persistence fails with:
  - `invalid memory alloc request size 1073741824` (1GB allocation)
- LangSmith rejects payloads exceeding 200MB
- LLM hallucinates API paths like `/conversions` (doesn't exist in GitHub API)
- Code generation fails after 3 retries with spec compliance violations

#### Evidence

Error messages from demo run:
```
WARNING: Failed to save checkpoint for persist_gold_checkpoint: invalid memory alloc request size 1073741824
Failed to send compressed multipart ingest: ... field size 573331091 exceeds maximum allowed size of 209715200 bytes
ERROR: Code generation failed
ValueError: Strict codegen failed for client 'GithubApiRootClient': spec compliance violations detected:
  [HIGH] Line 249: API path '/conversions' not found in specification
```

#### Suspected root cause

1. **Checkpoint bloat**: State includes large fields like `repo_snapshot.files`, `openapi_spec`, `full_markdown` that aren't excluded
2. **LLM context limits**: For large specs (1000+ endpoints), only first 30 endpoints shown to LLM
3. **No task-aware filtering**: The 30 endpoints shown aren't filtered by task relevance, so for "create issue" task, the issue-related endpoints (`/repos/{owner}/{repo}/issues`) may not be visible to LLM

#### Fix options

1) Minimal patch (chosen):
   - Expand checkpoint exclusion list (`repo_snapshot.files`, `full_markdown`, `content`, etc.)
   - Add max size check with fallback to minimal checkpoint
   - Filter endpoints by task relevance before showing to LLM
   - Increase visible endpoints from 30 to 50-100 with relevance scoring
2) Hybrid:
   - Stream large fields to separate tables instead of JSON blob
   - Use RAG to retrieve relevant endpoints instead of static list
3) Scalable redesign:
   - Move to chunked checkpoint storage
   - Implement semantic endpoint search for context building

#### Chosen direction (if decided)

- Minimal patch applied:
  - `src/integration_coworker/persistence/checkpoints.py`:
    - Extended `EXCLUDE_FIELDS` to include `files`, `content`, `full_markdown`, `repo_markdown_context`
    - Added `TRUNCATE_STRING_FIELDS` for large text fields
    - Added `deep_truncate()` to handle nested large structures
    - Added 100MB size limit with automatic fallback to minimal checkpoint
    - Added try/catch so checkpoint failures don't crash workflow
  - `src/integration_coworker/codegen/prompts.py`:
    - `_build_valid_paths_context()` now filters endpoints by task relevance for specs >100 endpoints
    - Shows top 100 most relevant endpoints with task keyword matching
    - Marks relevant endpoints with ★ for LLM guidance
  - `src/integration_coworker/graph/nodes/generate_code_and_tests.py`:
    - `_build_policy_feedback_prompt()` now filters retry endpoints by task relevance
    - Shows top 50 most relevant endpoints in retry prompts

Verification:

```bash
# Run with large spec
python -m integration_coworker.cli run \
  --spec-ref specs/github_api.json \
  --task "Create a new issue in a repository" \
  --repo-root /tmp/target --verbose
```

Expected: Checkpoint saves successfully (or gracefully degrades), LLM sees issue-related endpoints, code generation succeeds.

---

### BUG-0001 (placeholder)

- **Severity**: (blocker | high | medium | low)
- **Area**: (docs | persistence | llm | repo-integration | recovery | workflow | ui | ci)
- **Owner module(s)**: `src/...`
- **First seen in**: (Validation A/B/C/D/E)

#### Repro (exact commands)

```bash
# (paste exact command lines; keep values redacted if they include secrets)
```

#### Expected

- …

#### Actual

- …

#### Evidence

- File evidence:
  - `path/to/file.py` lines X–Y
- Failure output (redacted):

```text
...
```

#### Suspected root cause

- …

#### Fix options

1) Minimal patch: …
2) Hybrid: …
3) Scalable redesign: …

#### Chosen direction (if decided)

- …

---

### BUG-0002 — Postgres pgvector KG scoring test failures (graph_score + weights mismatch)

- **Severity**: high
- **Area**: persistence / retrieval
- **Owner module(s)**: `src/integration_coworker/retrieval/semantic_search.py`
- **First seen in**: Validation B

#### Repro (exact commands)

```bash
docker-compose up -d db

export DATABASE_URL='postgresql://integration:integration@localhost:5432/integration_coworker'
export POSTGRES_TESTS_REQUIRED=true
.venv311/bin/python -m pytest tests/test_pgvector_search.py -v --tb=short
```

#### Expected

- `tests/test_pgvector_search.py` should pass in Postgres-mode (pgvector).

#### Actual

- Two assertion failures occurred (no secrets involved):
  - Expected `graph_score == 0.3` when no provider_code is supplied, got `0.0`.
  - Expected combined scoring consistent with the test’s formula `(graph_score * 0.4) + (semantic_score * 0.6)`.

#### Evidence

- `tests/test_pgvector_search.py` (see KG template tests in `TestPythonSearch` and `TestPgvectorSearch`)
- `src/integration_coworker/retrieval/semantic_search.py` (KG template scoring in `_search_kg_templates_python` and `_search_kg_templates_pgvector`)

#### Suspected root cause

- KG template searches used `graph_score = 0.0` when `provider_code` was absent.
- KG template `combined_score` weights did not match the test’s expected weighting.

#### Fix options

1) Minimal patch (chosen):
  - Baseline `graph_score = 0.3` when `provider_code` is absent
  - `combined_score = (graph_score * 0.4) + (semantic_score * 0.6)`
2) Hybrid:
  - Make weights configurable *and* update tests to reflect per-provider overrides.
3) Scalable redesign:
  - Centralize all retrieval scoring in a single scoring module with explicit contracts + golden tests.

#### Chosen direction (if decided)

- Minimal patch applied in `src/integration_coworker/retrieval/semantic_search.py`.

Verification:

```bash
.venv311/bin/python -m pytest tests/test_pgvector_search.py -q
```

Result: `33 passed`

---

### BUG-0003 — Real LLM mode leaks key fragments in logs + does not fail fast on invalid credentials

- **Severity**: blocker (secrets safety + misleading “REAL” run semantics)
- **Area**: llm / logging
- **Owner module(s)**: `src/integration_coworker/llm/*` and any embedding client wrapper
- **First seen in**: Validation C

#### Repro (exact commands)

```bash
# (In a disposable clone is recommended)
export DATABASE_URL='postgresql://integration:integration@localhost:5432/integration_coworker'
export LLM_MODE=REAL
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o-mini

# OPENAI_API_KEY was set to an invalid value for this repro
python scripts/real_llm_smoke.py
```

#### Expected

- If provider credentials are invalid (401), the run should:
  - fail fast with a clean, redacted error
  - avoid emitting any credential fragments in logs
  - optionally stop the workflow rather than silently generating fallback skeleton outputs

#### Actual

- Provider 401 errors were logged repeatedly.
- Log messages included a partially redacted key string prefix (e.g., `sk-...****...`). Even partial key fragments are not acceptable in production logs.
- Workflow continued and produced fallback skeleton outputs, yielding a superficially “successful” summary despite REAL mode credential failure.
- Also observed: `ConnectionWrapper was garbage collected without being closed` warning.

#### Evidence

- Console output during Validation C (redacted in this log): shows 401 invalid API key errors with partially visible key prefix.

#### Suspected root cause

- Exception/log formatting includes raw provider error payloads.
- REAL mode does not enforce “hard fail” on auth errors; downstream steps still run using skeleton fallbacks.
- DB connection lifecycle warning suggests a missing context manager usage in an error path.

#### Fix options

1) Minimal patch:
  - Add a log redaction filter to scrub patterns like `sk-...` (and similar for Anthropic/LangSmith/etc.) from *all* logs.
  - Treat 401/403 from provider as a hard failure in REAL mode (raise and stop workflow).
2) Hybrid:
  - Add a credential preflight step (`whoami`/models list/embedding call) that runs once and fails fast before workflow execution.
3) Scalable redesign:
  - Centralize provider error handling in a single adapter that (a) maps errors to typed internal errors, (b) redacts safely, and (c) exposes policy: fail-fast vs fallback.

#### Chosen direction (if decided)

- Not decided yet.

---

### BUG-0004 — Repo detection snippet mis-read fields; build_effective_repo_profile arg order confusing

- **Severity**: low (but caused Validation D confusion)
- **Area**: repo-integration / detection
- **Owner module(s)**: `src/integration_coworker/repo/detection.py`, `src/integration_coworker/repo/models.py`
- **First seen in**: Validation D

#### Repro (exact commands)

```bash
# This prints None because DetectedProfile does not have .name/.primary_language/.notes
cd /Users/julianbartosz/git/repos/solver-agentic-spec-coworker
.venv311/bin/python - <<'PY'
from integration_coworker.repo.detection import detect_repo_profile
from pathlib import Path

repos = {
  "python-pkg": Path('/tmp/icw-repo-scan/python-pkg'),
  "monorepo": Path('/tmp/icw-repo-scan/monorepo'),
  "docs-src-split": Path('/tmp/icw-repo-scan/docs-src-split'),
}

for name, root in repos.items():
    profile = detect_repo_profile(str(root))
    print(name)
    print("  profile:", getattr(profile, 'name', None))
    print("  language:", getattr(profile, 'primary_language', None))
    print("  conf:", getattr(profile, 'confidence', None))
    print("  notes:", getattr(profile, 'notes', None))
PY
```

Also, calling `build_effective_repo_profile()` with the wrong argument order fails:

```bash
cd /Users/julianbartosz/git/repos/solver-agentic-spec-coworker
.venv311/bin/python - <<'PY'
from pathlib import Path
from integration_coworker.repo.detection import detect_repo_profile, build_effective_repo_profile

root = str(Path('/tmp/icw-repo-scan/python-pkg'))
dp = detect_repo_profile(root)

# WRONG: first arg must be DetectedProfile
build_effective_repo_profile(root, dp)
PY
```

#### Expected

- It should be harder to misuse the API from a quick “smoke snippet”, and the return shapes should be obvious.

#### Actual

- `DetectedProfile` printing was confusing (looked like “detector returned None fields”).
- Mis-ordered `build_effective_repo_profile()` call raises:
  - `AttributeError: 'str' object has no attribute 'language'`

#### Evidence

- `src/integration_coworker/repo/detection.py`
  - `detect_repo_profile()` returns `DetectedProfile` (fields: `archetype_name`, `language`, `confidence`, `evidence`, ...)
  - `build_effective_repo_profile(detected: DetectedProfile, repo_root: Optional[str], ...)` has non-intuitive argument order for interactive use.
- `src/integration_coworker/repo/models.py`
  - `DetectedProfile` does not define `.name`, `.primary_language`, or `.notes`

#### Suspected root cause

- Legacy mental model / examples assumed a different profile type.
- `build_effective_repo_profile()` signature is easy to call incorrectly in ad-hoc scripts.

#### Fix options

1) Minimal patch:
   - Add a small helper function with “ergonomic” signature, e.g. `get_repo_profile(repo_root: str, *, use_llm_refinement: bool=False) -> RepoProfile`.
   - Add a docstring example snippet that prints correct fields (`DetectedProfile.language`, etc.).
2) Hybrid:
   - Reorder args of `build_effective_repo_profile()` to `(repo_root, detected, ...)` and provide a backwards-compatible shim.
3) Scalable:
   - Introduce a typed “DetectionResult” wrapper that contains both `DetectedProfile` and `RepoProfile` and can render a concise summary.

#### Chosen direction (if decided)

- Not decided yet.

---

### BUG-0005 — End-to-end run emits credential fragments in 401 errors + continues with skeleton outputs (REAL-ish behavior)

- **Severity**: blocker
- **Area**: llm / logging / workflow semantics
- **Owner module(s)**: `src/integration_coworker/llm/*`, embedding client wrappers, workflow error policies
- **First seen in**: Validation E (dry-run on minimal repo)

#### Repro (exact commands)

```bash
cd /Users/julianbartosz/git/repos/solver-agentic-spec-coworker

# In this environment, OPENAI_API_KEY was set but invalid/unauthorized.
# Run is done against a disposable local git repo.
.venv311/bin/python - <<'PY'
from pathlib import Path
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions

repo_root = Path('/tmp/icw-e2e-minrepo')

design_and_generate_integration(
  spec_refs=['specs/httpbin_api.json'],
  task_description='Add a minimal httpbin integration example. Keep changes small.',
  repo_root=repo_root,
  options=IntegrationOptions(dry_run=True),
)
PY
```

#### Expected

- If the provider returns 401/invalid credentials:
  - logs must not include any key/token fragments (even partially masked)
  - the run should fail fast (or at least surface a hard error) in REAL mode
  - the system should not silently proceed with “fallback skeleton” outputs that look successful

#### Actual

- Multiple 401 errors surfaced with partially redacted key fragments in message text (`sk-...`).
- The system continued and logged multiple fallbacks like “Falling back to python skeleton … because LLM refinement failed …”.
- Additional warning observed:
  - `ConnectionWrapper was garbage collected without being closed` (DB lifecycle on error path)

#### Evidence

- Terminal output during Validation E (redacted in this log) contained repeated 401 error payloads with partial key fragments.
- Related warning appeared in-session:
  - `ConnectionWrapper was garbage collected without being closed. Use 'with db.get_connection() as conn:' pattern ...`

#### Suspected root cause

- Provider exception messages are being logged directly (no global redaction filter).
- Workflow treats LLM/embedding failures as non-fatal in paths that influence codegen + repo config inference.
- DB connection context managers aren’t consistently applied on error paths.

#### Fix options

1) Minimal patch:
   - Add a global log redaction filter (scrub known token patterns in *all* log records).
   - Treat 401/403 as terminal in REAL mode (raise + stop workflow).
   - Ensure DB connections are always managed via context manager in failing paths.
2) Hybrid:
   - Add a separate “credentials preflight” step executed once at startup.
   - Make fail-fast/fallback policy explicit via a single `LLMFailurePolicy`.
3) Scalable redesign:
   - Centralize provider adapters so errors are mapped to typed internal errors and can be safely rendered without raw payloads.

#### Chosen direction (if decided)

- Not decided yet.
