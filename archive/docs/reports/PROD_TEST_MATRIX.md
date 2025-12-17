# Production Test Matrix

Authoritative, runnable checks for production posture. Commands assume macOS zsh from repo root unless noted. Use smallest viable fixtures and capture proof artifacts.

## 1) Postgres persistence (Docker OK)
- **Start DB**
	- `docker compose up -d postgres`
- **Env**
	- `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/integration_coworker`
	- `USE_SQLITE=false`
- **Run**
	- `PYTHONPATH=src DATABASE_URL=... USE_SQLITE=false python -m pytest tests/test_m4_persistence.py -q`
	- `PYTHONPATH=src DATABASE_URL=... USE_SQLITE=false python -m pytest tests/test_m4_generated_code_execution.py -q`
- **Proof (psql)**
	- `psql "$DATABASE_URL" -c "\dt" | grep workflow_templates`
	- `psql "$DATABASE_URL" -c "SELECT run_id,status FROM integration_gold.checkpoints ORDER BY created_at DESC LIMIT 5;"`
	- `psql "$DATABASE_URL" -c "SELECT template_key,confidence_score FROM kg.feedback_records ORDER BY updated_at DESC LIMIT 5;"`
- **Expected artifacts**
	- Tables `workflow_templates`, `kg.feedback_records`, `integration_gold.checkpoints` exist.
	- Recent rows for the run_id under test; confidence rows updated after feedback hooks.

## 2) Real LLM mode (cost-capped)
- **Env**
	- `LLM_MODE=real`
	- `LLM_PROVIDER=openai` (or target provider)
	- `OPENAI_API_KEY=<required>` (hard blocker if missing)
	- `LANGCHAIN_TRACING_V2=true`
	- `LANGCHAIN_PROJECT=prod-matrix-llm`
	- `PYTHONPATH=src`
- **Fixture**
	- `tests/fixtures/mock_payments_openapi.yaml`
- **Run (minimal e2e)**
	- `PYTHONPATH=src LLM_MODE=real LLM_PROVIDER=openai LANGCHAIN_TRACING_V2=true LANGCHAIN_PROJECT=prod-matrix-llm OPENAI_API_KEY=$OPENAI_API_KEY python -m pytest tests/test_end_to_end_integration.py::test_minimal_flow -q`
- **Proof artifacts**
	- LangSmith trace visible for run with `task_type=integration` and model/provider metadata.
	- Local log includes `run_id=` and no `MockLLM` warnings.
	- Generated code artifacts stored in `.outputs/` (if configured) or reported in test output.

## 3) Repo structure diversity (3 archetypes)
- **Env baseline**: `PYTHONPATH=src LLM_MODE=mock USE_SQLITE=true`
- **Single-package repo**
	- `python -m pytest tests/test_repo_file_writes.py::test_single_package -q`
	- Expect repo profile detection for flat src, codegen path writes into package root; artifacts lint clean.
- **Monorepo**
	- `python -m pytest tests/test_repo_file_writes.py::test_monorepo -q`
	- Expect detection of multiple services; codegen chooses service-specific root; gold/silver persisted to SQLite.
- **Nested infra-heavy**
	- `python -m pytest tests/test_repo_file_writes.py::test_nested_infra -q`
	- Expect heuristics to ignore infra dirs, select app module; codegen paths set accordingly; test asserts correct placement.
- **Proof artifacts**
	- For each: temporary repo tree shows generated client/flow/tests under expected subdir; pytest passes.
	- Logs show `repo_profile` with language/extensions matching fixture archetype.

## 4) Real target repo sandbox integration
- **Prep**
	- `git clone <target_repo_url> /tmp/integration-sandbox`
	- `pushd /tmp/integration-sandbox`
- **Run integration**
	- `PYTHONPATH=/Users/julianbartosz/git/repos/solver-agentic-spec-coworker/src LLM_MODE=mock USE_SQLITE=true python -m integration_coworker.cli run --spec /path/to/spec.yaml --task "<task desc>" --repo-root . --dry-run`
- **Apply generated changes**
	- Review `repo_changes` summary; apply patch or write files as indicated under sandbox root.
- **Target repo checks**
	- Run native lint/tests, e.g. `npm test` or `pytest` depending on repo language.
- **Expected artifacts**
	- Generated client/flow/tests inside sandbox repo; integration_coworker report markdown.
	- Native repo CI steps (lint/test) pass or failures captured for backlog.
