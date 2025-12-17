# V1 Production Gap Backlog

Ranked gaps discovered while executing the production test matrix (Step 3/6). Each entry cites the failing command, stack excerpt, hypothesized root cause, and linked test coverage.

## Gaps (ranked)

1. **[P0] Real LLM execution blocked by invalid API key**  
	- **Command:** `PYTHONPATH=src LLM_MODE=real ... python3 -m pytest tests/test_m4_generated_code_execution.py -q --tb=short`  
	- **Excerpt:** `Error code: 401 - Incorrect API key provided ...` → generated code missing `api_key`/`payload` params, assertion failed.  
	- **Root cause:** OpenAI key `OPENAI_API_KEY` is invalid; all LLM calls (embed_spec_chunks, understand_task, attach_policies, generate_code_and_tests) fall back to skeletons, breaking structure expectations.  
	- **Test link:** `tests/test_m4_generated_code_execution.py::test_generated_code_has_correct_structure` (fails).  
	- **Action:** Provide valid provider key (or run in mock mode for CI) and/or gate real-LLM suite behind secrets check. Blocks real LLM path signoff.

2. **[P1] Postgres path missing required Python deps by default**  
	- **Command:** `DATABASE_URL=... USE_SQLITE=false PYTHONPATH=src python3 -m pytest tests/test_m4_persistence.py -q --tb=short` (initial runs).  
	- **Excerpt:** `ImportError: psycopg and psycopg_pool are required ...` then `ModuleNotFoundError: No module named 'langgraph.checkpoint.postgres'`.  
	- **Root cause:** Base environment lacks `psycopg[binary]`, `psycopg_pool`, and `langgraph-checkpoint-postgres`.  
	- **Test link:** `tests/test_m4_persistence.py::*` (failed until deps installed).  
	- **Action:** Add these packages to `requirements.txt`/`pyproject` or document as required extras for Postgres mode.

3. **[P2] Docker compose service name mismatch in instructions**  
	- **Command:** `docker compose up -d postgres` → `no such service: postgres`; service is named `db`.  
	- **Root cause:** Compose file defines `services.db`; docs/scripts refer to `postgres`.  
	- **Test link:** Manual compose startup step for matrix section 1.  
	- **Action:** Update matrix/docs to use `docker compose up -d db` (done in matrix) and/or add alias.

4. **[P2] Redis cache optional but noisy**  
	- **Command:** real LLM run above.  
	- **Excerpt:** Repeated `redis package not installed. LLM cache disabled.` warnings.  
	- **Root cause:** Cache dependency optional; not installed by default.  
	- **Test link:** same as Gap #1 run.  
	- **Action:** Decide whether to include `redis` in prod deps or silence warning when cache disabled.

5. **[P1] Sandbox integration step unverified (no target repo provided)**  
	- **Command placeholder:** `integration_coworker.cli run ... --repo-root /tmp/integration-sandbox` not executed.  
	- **Root cause:** No target repo URL/task provided; cannot validate file writes and downstream repo CI.  
	- **Test link:** N/A (manual scenario).  
	- **Action:** Select a representative target repo and run sandbox flow to validate code writes + native lint/test.

## Notes on executed checks (for provenance)
- Postgres persistence tests now **pass** after installing missing deps: `tests/test_m4_persistence.py` (3 passed).  
- Repo detection archetype coverage: `tests/repo/test_detection_profiles_e2e.py::TestFastAPIServiceDetection::test_detects_fastapi_archetype` and `::TestDjangoServiceDetection::test_detects_django_archetype` **pass**; `tests/repo/test_detection_edge_cases.py::TestDetectionRobustness::test_monorepo_structure` **passes** for monorepo handling.  
- Real LLM path remains blocked until a valid key is supplied.
