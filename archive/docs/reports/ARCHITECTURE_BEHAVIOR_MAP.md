# Architecture & Behavior Map

Source-first notes on runtime modes, environment flags, and control flows. All references are to the code checked today.

## LLM modes & providers
- **Mode resolution:** `config/llm_mode.py` → `LLM_MODE` (real/mock/record/replay) with legacy `USE_MOCK_LLM=true` mapping to MOCK. `get_llm_mode()` caches the value.
- **Provider/model overrides:** `config/__init__.py::_apply_env_overrides` applies `LLM_PROVIDER`, `LLM_MODEL`, `USE_MOCK_LLM` to archetype model blocks with compatibility guardrails; incompatible model → reset to provider default (openai=gpt-4o, anthropic=claude-sonnet-4-5-20250929, google=gemini-2.5-flash).
- **Sync client selection:** `llm/client.py::get_llm_client` and `get_llm_client_for_archetype`
  - Mode MOCK or provider "mock" → `MockLLMClient`.
  - Provider fallback chains: primary → OpenAI → Anthropic → Google (archetype path) → mock unless `strict=True` (then error if no keys).
  - API keys from env: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`; optional `LLM_BASE_URL` for OpenAI.
  - Record/replay: when `LLM_MODE=record|replay`, interactions stored under `.llm_recordings/` keyed by prompt/system/model. REPLAY hard-fails if missing.
- **Async client path:** `llm/async_client.py` mirrors mode checks, cache usage, and record/replay; recommended entrypoint for nodes (`call_llm_async_for_node`).
- **Tracing:** LangSmith auto-tracing when `LANGCHAIN_TRACING_V2=true`; metadata includes `task_type`, model, provider. CLI also prints project from `LANGCHAIN_PROJECT` when enabled.
- **Caching:** `llm/cache.py` uses Redis with `REDIS_URL`, `LLM_CACHE_ENABLED` (default true), `LLM_CACHE_TTL` (default 86400). Both sync/async clients consult the cache before calling providers and write on success.
- **Embedding model:** `config.get_embedding_config()` pulls `EMBEDDING_MODEL` override, dimensions 1536. `graph/nodes/embed_spec_chunks.py` uses LangChain `OpenAIEmbeddings` when `OPENAI_API_KEY` is set and not in mock mode.
- **Test execution gating:** `runtime/test_execution.py` only runs when `ENABLE_TEST_EXECUTION=true` (timeout via `TEST_EXECUTION_TIMEOUT`).

## Database & persistence
- **Engine selection:** `config.DatabaseConfig` → `USE_SQLITE` (true → SQLite) else Postgres from `DATABASE_URL` (default `postgresql://postgres:postgres@localhost:5432/integration_coworker`). `get_engine_type()` returns `postgres|sqlite`.
- **SQLite posture:** `persistence/db.py::get_connection` emits deprecation warning unless `PYTEST_CURRENT_TEST` is set. SQLite path can be overridden via `SQLITE_PATH`.
- **Postgres requirements:** Requires psycopg/pg pool; failures are hard errors unless explicitly on SQLite. Schema bootstrap in `persistence/postgres.py` vs `_init_sqlite_schema` for tests.
- **LangGraph checkpoints:** `graph/runtime.py::get_checkpointer` tries `DATABASE_URL` via `AsyncPostgresSaver`; on failure falls back to file-based SQLite `data/langgraph_checkpoints.db`. Cache disabled by default to avoid cross-loop locks.
- **Streaming persistence:** `config.Settings.streaming_persistence` (`STREAMING_PERSISTENCE` = auto|true|false). Auto triggers when `STREAMING_THRESHOLD_BYTES`≥500KB or `STREAMING_THRESHOLD_CHUNKS`≥500. Helpers `is_streaming_persistence_enabled/disabled` and `should_use_streaming_for_spec` gate streaming in nodes (e.g., `embed_spec_chunks`).
- **Dry-run vs persist:** CLI `run` command uses `--dry-run/-n` (default False) to skip DB writes; other commands default to dry-run true (`--dry-run/--persist`).
- **Resume:** CLI supports `--auto-resume` (provider-scoped) and `--resume <run_id>`; uses checkpoint APIs in `persistence.checkpoints`.

## Pattern learning & KG
- **Master switch:** `PATTERN_LEARNING_ENABLED` (default false) gates all pattern learning writes (`config.Settings`).
- **Granular flags:** `PATTERN_CAPTURE_EVENTS`, `PATTERN_DISCOVER_CANDIDATES`, `PATTERN_AUTO_PROMOTE`, `PATTERN_MATCH_LEARNED` with defaults true/true/false/true respectively. Promotion thresholds: `PATTERN_PROMOTION_THRESHOLD` (default 3) and `PATTERN_MIN_FEEDBACK_SCORE` (0.6); decay via `PATTERN_CONFIDENCE_DECAY` (0.95).
- **KG confidence/feedback:** Stored in `kg_feedback_records`/`confidence_history`; CLI exposes `kg-confidence` commands; confidence updates live in `feedback/confidence.py`.

## Repo detection & codegen controls
- **Repo detection:** `repo/detection.py` uses heuristics over filesystem; if `repo_root` provided, infers roots/tests/extension mapping. If confidence low and repo available, `_refine_profile_with_llm` calls `get_llm_client_for_node("plan_run")` to refine layout (skips when mock responds).
- **Provider override:** CLI `run --provider` sets `override_provider_code` in `IntegrationOptions` used by runtime.
- **Codegen strictness:** CLI flags `--strict-codegen` (auto-format + fail on validation) and `--constrained-codegen` (inject paths/fixtures to reduce hallucination); propagated via `IntegrationOptions` to nodes.
- **Policy mode:** `--policy-mode inline|runtime` (alias `--standalone` → inline) drives generated artifact shape.
- **Strict codegen execution path:** `graph/nodes/generate_code_and_tests.py::_refine_with_llm` applies ruff fixes (Python), tree-sitter/AST syntax validation, and security validation; on failure in strict mode it raises, otherwise falls back to a language-aware skeleton template via `codegen.prompts.get_skeleton_template`. Policy validation (SEC-004) can auto-fix hallucinated paths before retry; constrained_codegen is checked first for path guarding.
- **Write-path & conflict handling:** `persist_gold_checkpoint` writes workflow templates with `ON CONFLICT`-style lookup (select then insert) and binds workflow_template.id on state; feedback hooks record per-template compile/test/syntax outcomes (`feedback/hooks.py`) without breaking the main flow.
- **Validation gates:** Syntax → security (Python) → policy validation; failures in non-strict mode fall back to skeletons, strict mode raises. Feedback is recorded via `safe_record_*` but isolated from control flow.

## Observability & tracing
- **LangSmith:** `graph/runtime.py` wraps nodes with `timed_node`; when `LANGCHAIN_TRACING_V2=true` and langsmith installed, uses `@traceable`. Run context (`run_id`, provider_code) is propagated via `llm.client.set_run_context` during graph execution.
- **Token accounting:** `llm/client.py` aggregates token usage per run when `init_token_usage` invoked.
- **Logging:** CLI `--verbose` flips logger to DEBUG; JSON logs toggle via `logging_config.JSON_LOGS` (see `logging_config.py`).

## UI & recovery flows
- **Streamlit UI:** `ui/streamlit_app.py` wraps `design_and_generate_integration` with error capture; stores `last_error`/`last_result`/`run_history` in session state. Tabs show run status, artifacts, graph trace, and an Errors & Recovery panel.
- **Recovery actions (UI):** From the Errors tab users can **Retry** (re-run with captured inputs), **Restart** (clear state), or **Skip** (placeholder; warns that checkpoint support is needed). Retry and restart call back into `_run_integration`; skip only warns.
- **Recovery APIs:** Programmatic helpers in `api/recovery.py` implement `retry_from_last_failure`, `resume_run` (checkpoint resume via persistence.checkpoints + runtime), and `skip_failing_step` (skip cascade with dependency analysis). Requires checkpoints; clears checkpoints on successful resume.
- **IntegrationOptions semantics:** `IntegrationOptions` carries `dry_run`, `repo_integration_enabled`, provider/task overrides, `policy_mode`, `no_cache`, `strict_codegen`, `constrained_codegen`. CLI and UI build options and pass through to `plan_run`/`generate_code_and_tests` for behavior gating.

## Safety & fallbacks
- **Mock behavior:** `llm/client.py::is_mock_llm_mode` warns when no API keys and not explicitly mock; archetype provider "mock" or `USE_MOCK_LLM=true` forces mock clients.
- **Record/replay:** `.llm_recordings/` is the on-disk store for RECORD/REPLAY modes; REPLAY without a hit raises.
- **Embedding failure:** If `OPENAI_API_KEY` missing or in mock, `_get_embedding_client` returns None → node likely uses synchronous fallback logic (tests may inject fixtures).
- **Test execution disabled by default** to avoid untrusted code execution; opt-in via `ENABLE_TEST_EXECUTION`.

## Quick flag matrix (env → behavior)
- `LLM_MODE` / `USE_MOCK_LLM` → select mock/real/record/replay across sync+async LLM clients.
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `LLM_BASE_URL` → provider auth/config; absence triggers fallback chain to other providers then mock unless `strict=True`.
- `LANGCHAIN_TRACING_V2` (and optional `LANGCHAIN_PROJECT`) → enable LangSmith tracing/metadata.
- `LLM_CACHE_ENABLED`, `REDIS_URL`, `LLM_CACHE_TTL` → Redis-backed response cache on both sync/async paths.
- `DATABASE_URL`, `USE_SQLITE`, `SQLITE_PATH` → select persistence backend; SQLite warns outside pytest.
- `STREAMING_PERSISTENCE`, `STREAMING_THRESHOLD_BYTES`, `STREAMING_THRESHOLD_CHUNKS` → streaming persistence gating.
- `PATTERN_*` flags → enable pattern learning pipeline + promotion thresholds.
- `ENABLE_TEST_EXECUTION`, `TEST_EXECUTION_TIMEOUT` → runtime test sandbox control.