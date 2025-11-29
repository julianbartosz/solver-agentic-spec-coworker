# M5 Roadmap & Implementation Plan

**Document Version**: 1.0  
**Last Updated**: November 28, 2025  
**Target Completion**: Q1 2026

---

## 1. Goals for M5

1. **Production-ready multi-provider support** — Add 2-3 real providers (Stripe Checkout, HubSpot, GitHub) with full test coverage
2. **KG-first workflow selection** — Ensure the happy path uses KG templates without in-memory fallback
3. **Improved repo awareness** — Profile detection working reliably, import paths always correct
4. **Observability polish** — LangSmith traces documented, verbose mode for debugging
5. **CI/CD integration** — Automated testing, version tagging, release workflow

---

## 2. Workstreams

### Workstream A: Multi-Provider Support

Prove the system is general by adding real providers beyond mock_payments.

#### A1: Add Stripe Checkout (Sessions API)

**Status**: ✅ Partially complete (Payment Intents added)

**Remaining work:**
- Add Stripe Checkout Sessions OpenAPI fixture
- Add workflow templates for `create_checkout_session`, `list_sessions`, `expire_session`
- Add e2e test similar to `test_stripe_integration.py`

**Files to update:**
- `tests/fixtures/stripe_checkout_openapi.yaml` (new)
- `src/integration_coworker/graph/nodes/align_task_with_kg.py`
- `tests/test_stripe_checkout.py` (new)

**Definition of done:**
- [ ] 5+ tests for Stripe Checkout passing
- [ ] Generated code uses correct Stripe paths (`/v1/checkout/sessions`)
- [ ] KG populated with Stripe Checkout templates after run

**Estimate:** S (4-6 hours)

---

#### A2: Add HubSpot Contacts API

**Description:** Add HubSpot as a CRM provider to show non-payment use case.

**Files to create/update:**
- `tests/fixtures/hubspot_contacts_openapi.yaml` (new)
- `src/integration_coworker/graph/nodes/align_task_with_kg.py` — Add HubSpot templates
- `tests/test_hubspot_integration.py` (new)

**Workflow templates to add:**
- `("hubspot", "create_contact")` — POST /crm/v3/objects/contacts
- `("hubspot", "get_contact")` — GET /crm/v3/objects/contacts/{id}
- `("hubspot", "update_contact")` — PATCH /crm/v3/objects/contacts/{id}

**Definition of done:**
- [ ] HubSpot OpenAPI fixture exists
- [ ] 3+ workflow templates in `_LEGACY_WORKFLOW_TEMPLATES`
- [ ] 5+ e2e tests passing
- [ ] Generated code has HubSpot-specific naming

**Estimate:** M (6-8 hours)

---

#### A3: Add GitHub Issues API

**Description:** Add GitHub as a DevOps provider.

**Files to create/update:**
- `tests/fixtures/github_issues_openapi.yaml` (new)
- `src/integration_coworker/graph/nodes/align_task_with_kg.py`
- `tests/test_github_integration.py` (new)

**Workflow templates to add:**
- `("github", "create_issue")` — POST /repos/{owner}/{repo}/issues
- `("github", "get_issue")` — GET /repos/{owner}/{repo}/issues/{issue_number}
- `("github", "list_issues")` — GET /repos/{owner}/{repo}/issues

**Definition of done:**
- [ ] GitHub OpenAPI fixture exists
- [ ] 3+ workflow templates
- [ ] 5+ e2e tests passing
- [ ] Demonstrates path parameter handling (`{owner}`, `{repo}`)

**Estimate:** M (6-8 hours)

---

#### A4: Provider inference from spec content

**Description:** Infer `provider_code` from OpenAPI `info.title` or `servers[0].url` when not provided.

**Files to update:**
- `src/integration_coworker/graph/nodes/ingest_spec.py`
- `src/integration_coworker/graph/nodes/detect_and_parse_spec.py`

**Definition of done:**
- [ ] `provider_code` correctly inferred from Stripe, HubSpot, GitHub specs
- [ ] Test: running without `--provider` flag works for known providers
- [ ] Falls back to filename-based inference for unknown specs

**Estimate:** S (2-3 hours)

---

### Workstream B: KG / GraphRAG Hardening

Ensure the Knowledge Graph is the primary source of workflow templates.

#### B1: Add `kg-query` CLI command

**Description:** CLI command to test GraphRAG retrieval with scoring details.

```bash
integration-coworker kg-query \
  --provider stripe \
  --task "Create a payment for a subscription"
```

**Output:**
- Top 5 matching templates with scores
- Score breakdown (graph score, embedding score, exact match bonus)

**Files to create/update:**
- `src/integration_coworker/cli.py` — Add `kg-query` command
- `src/integration_coworker/kg/__init__.py` — Expose scoring details

**Definition of done:**
- [ ] `kg-query` command works
- [ ] Shows score breakdown for each match
- [ ] JSON output mode available

**Estimate:** S (3-4 hours)

---

#### B2: Test: Second run uses KG template (no fallback)

**Description:** Prove that after a successful run, subsequent runs find templates from KG.

**Files to update:**
- `tests/test_graphrag_integration.py` — Add stricter assertion

**Definition of done:**
- [ ] Test asserts `candidate_templates[0]["template_id"]` is not None
- [ ] Test asserts in-memory fallback was NOT used
- [ ] Test runs with `USE_IN_MEMORY_KG_FALLBACK=0`

**Estimate:** S (1-2 hours)

---

#### B3: KG metrics in run report

**Description:** Add GraphRAG metrics to the markdown report.

**Metrics to add:**
- Number of templates considered
- Top template score
- Graph score vs embedding score breakdown

**Files to update:**
- `src/integration_coworker/graph/nodes/build_report.py`
- `src/integration_coworker/graph/state.py` — Add `kg_metrics` field

**Definition of done:**
- [ ] Report includes "## KG Retrieval" section
- [ ] Shows template selection reasoning
- [ ] Metrics visible in both dry-run and persist modes

**Estimate:** S (2-3 hours)

---

#### B4: Embedding-based template matching

**Description:** Ensure embeddings are generated and used for semantic similarity.

**Current state:** Embeddings are stored but may not be used in mock LLM mode.

**Files to update:**
- `src/integration_coworker/kg/__init__.py` — Verify embedding query path
- `src/integration_coworker/graph/nodes/persist_kg_learning.py` — Ensure embeddings are saved

**Definition of done:**
- [ ] Test with real embeddings shows improved matching
- [ ] Mock mode falls back gracefully to exact/graph match
- [ ] Embedding dimension matches pgvector schema (1536)

**Estimate:** M (4-6 hours)

---

### Workstream C: Repo Integration Polish

Ensure generated code fits perfectly into target repositories.

#### C1: Improve `detect_repo_profile` coverage

**Description:** Add detection for more archetypes.

**Archetypes to add:**
- Flask (`app.py` + `flask` in requirements)
- Express.js (`package.json` + `express` dependency)
- NestJS (`package.json` + `@nestjs/core`)

**Files to update:**
- `src/integration_coworker/repo/profiles.py`
- `tests/test_repo_profiles.py`

**Definition of done:**
- [ ] Flask, Express, NestJS detection tests pass
- [ ] Each has appropriate RepoProfile with layout hints

**Estimate:** M (4-5 hours)

---

#### C2: Fix relative import issues

**Description:** Generated flow code uses relative imports that may not work.

**Current issue:** Flow imports client with `from .clients.X` which requires package structure.

**Solution options:**
1. Use absolute imports (`from integrations.clients.X`)
2. Dynamically compute import path from RepoProfile

**Files to update:**
- `src/integration_coworker/graph/nodes/generate_code_and_tests.py`
- `src/integration_coworker/codegen/paths.py`

**Definition of done:**
- [ ] Generated code imports work without modification
- [ ] `test_m4_generated_code_execution.py` no longer needs workaround
- [ ] Import paths respect RepoProfile.integrations_root

**Estimate:** M (4-6 hours)

---

#### C3: Post-generation import validation

**Description:** Add a validation step that tries to import generated modules.

**Files to create/update:**
- `src/integration_coworker/graph/nodes/validate_integration_design.py` — Add import check
- Or new node: `validate_imports.py`

**Definition of done:**
- [ ] Validation catches import errors before returning result
- [ ] Errors are added to `state.errors` with fix suggestions
- [ ] Test proves validation catches bad imports

**Estimate:** S (3-4 hours)

---

### Workstream D: Observability & Ergonomics

Make the system easier to debug and operate.

#### D1: Document LangSmith integration

**Description:** Add docs explaining how to set up LangSmith tracing.

**Files to create:**
- `docs/LANGSMITH_SETUP.md`

**Content:**
- Environment variables needed
- How to view traces
- What's traced (LLM calls, embeddings)
- Example screenshots

**Definition of done:**
- [ ] Doc exists and is accurate
- [ ] Linked from DEMO.md
- [ ] Includes troubleshooting tips

**Estimate:** S (2 hours)

---

#### D2: Add `--verbose` CLI flag

**Description:** Add verbose output showing key decisions during run.

**What to log:**
- Selected RepoProfile
- Chosen workflow template
- Matched endpoints
- Code generation fallback reasons

**Files to update:**
- `src/integration_coworker/cli.py`
- Various node files — Add verbose logging

**Definition of done:**
- [ ] `--verbose` flag works on `run` and `demo` commands
- [ ] Output shows decision reasoning
- [ ] Not too noisy for normal use

**Estimate:** M (4-5 hours)

---

#### D3: Troubleshooting section in DEMO.md

**Description:** Add common issues and solutions.

**Issues to cover:**
- Database not initialized
- Mock LLM mode not set
- Import path issues
- KG empty after run

**Definition of done:**
- [ ] DEMO.md has troubleshooting section
- [ ] Each issue has clear solution
- [ ] Links to relevant docs/commands

**Estimate:** S (1-2 hours)

---

#### D4: Health check endpoint (optional)

**Description:** Add CLI command or function to verify system is configured correctly.

```bash
integration-coworker health
```

**Checks:**
- Database connection
- pgvector extension (if Postgres)
- LLM configuration (API key or mock mode)
- Required tables exist

**Definition of done:**
- [ ] `health` command exists
- [ ] Returns clear pass/fail for each check
- [ ] Exit code reflects overall health

**Estimate:** S (2-3 hours)

---

## 3. Priority Matrix

| ID | Item | Priority | Effort | Impact |
|----|------|----------|--------|--------|
| A1 | Stripe Checkout | P0 | S | High |
| A2 | HubSpot Contacts | P0 | M | High |
| B2 | KG template test | P0 | S | Medium |
| C2 | Fix relative imports | P0 | M | High |
| A3 | GitHub Issues | P1 | M | Medium |
| B1 | kg-query CLI | P1 | S | Medium |
| B3 | KG metrics in report | P1 | S | Medium |
| C1 | More archetypes | P1 | M | Medium |
| C3 | Import validation | P1 | S | High |
| D1 | LangSmith docs | P1 | S | Medium |
| D2 | Verbose flag | P2 | M | Low |
| D3 | Troubleshooting | P2 | S | Medium |
| A4 | Provider inference | P2 | S | Low |
| B4 | Embedding matching | P2 | M | Medium |
| D4 | Health check | P2 | S | Low |

---

## 4. Timeline (Suggested)

### Week 1: Multi-Provider Foundation
- [ ] A1: Stripe Checkout
- [ ] A2: HubSpot Contacts
- [ ] B2: KG template test

### Week 2: Repo Polish
- [ ] C2: Fix relative imports
- [ ] C3: Import validation
- [ ] C1: More archetypes

### Week 3: Observability
- [ ] B1: kg-query CLI
- [ ] B3: KG metrics in report
- [ ] D1: LangSmith docs

### Week 4: Polish & Documentation
- [ ] A3: GitHub Issues
- [ ] D2: Verbose flag
- [ ] D3: Troubleshooting
- [ ] Final testing & release

---

## 5. Definition of Done for M5

- [ ] 3+ providers with full test coverage
- [ ] KG is the primary template source (no in-memory fallback in happy path)
- [ ] Generated code imports work without modification
- [ ] 5+ repo archetypes detected
- [ ] 175+ tests passing
- [ ] DEMO.md updated with new providers
- [ ] LangSmith docs exist
- [ ] Tagged release `m5-complete`

---

*Document prepared by Agentic Integration Co-Worker*
