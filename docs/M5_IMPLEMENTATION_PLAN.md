# M5 Tactical Implementation Plan

**Document Version**: 1.0  
**Created**: November 28, 2025  
**Engineer**: Single IC (4-week sprint)  
**Baseline**: `m4-demo-ready` tag (158 tests passing)

---

## Executive Summary

M5 focuses on **production hardening** across four workstreams:
1. **Multi-Provider Scale** — Add 2 more providers (HubSpot, GitHub) to prove generality
2. **KG/GraphRAG Maturity** — Make KG the primary path, add observability
3. **Codegen Polish** — Fix import paths, validate generated code actually runs
4. **Observability & Ergonomics** — Verbose mode, health checks, documentation

**Target**: 180+ tests, 4+ providers, zero import hacks needed.

---

## Current State Analysis

### What Works Well (M4)
| Component | Evidence |
|-----------|----------|
| 18-node LangGraph workflow | `runtime.py` — full graph runs e2e |
| SQLite + Postgres persistence | `test_m4_persistence.py` — 3 tests |
| GraphRAG retrieval | `kg/__init__.py` — 40/40/20 scoring |
| 2 providers | `mock_payments`, `stripe` (Payment Intents) |
| 3 repo profiles | FastAPI, Django, Next.js detection |
| KG population | `persist_kg_learning.py` — writes nodes/edges/steps |

### Known Gaps
| Gap | Location | Impact |
|-----|----------|--------|
| Import path hack in test | `test_m4_generated_code_execution.py:86` | Flow → client import breaks |
| Only Payment APIs | No CRM, DevOps providers | Can't prove generality |
| No KG metrics in report | `build_report.py` | No visibility into template selection |
| No `--verbose` flag | `cli.py` | Hard to debug decision-making |
| No health check | Missing | Hard to diagnose config issues |

---

## Workstream 1: Multi-Provider Scale

**Goal**: Prove the system handles non-payment APIs with different shapes.

### WS1-T1: Add HubSpot Contacts API (CRM)
**Priority**: P0 | **Effort**: M (6-8h)

**Files to create/modify**:
| File | Nature of Change |
|------|------------------|
| `tests/fixtures/hubspot_contacts_openapi.yaml` | NEW — OpenAPI spec with 4 endpoints |
| `src/integration_coworker/graph/nodes/align_task_with_kg.py` | ADD — 3 templates to `_LEGACY_WORKFLOW_TEMPLATES` |
| `tests/test_hubspot_integration.py` | NEW — 6 tests mirroring `test_stripe_integration.py` |

**OpenAPI Structure**:
```yaml
paths:
  /crm/v3/objects/contacts:
    post:  # CreateContact
    get:   # ListContacts
  /crm/v3/objects/contacts/{contactId}:
    get:   # GetContact
    patch: # UpdateContact
```

**Templates to add**:
```python
("hubspot", "create_contact"): { ... 5-step flow ... }
("hubspot", "get_contact"): { ... 5-step flow ... }
("hubspot", "update_contact"): { ... 5-step flow ... }
```

**Definition of Done**:
- [ ] `hubspot_contacts_openapi.yaml` parses correctly (test in `test_build_silver_api_model.py`)
- [ ] 3 workflow templates in legacy dict
- [ ] 6 tests in `test_hubspot_integration.py` passing:
  - `test_hubspot_spec_parse`
  - `test_hubspot_create_contact_e2e`
  - `test_hubspot_get_contact_e2e`
  - `test_hubspot_update_contact_e2e`
  - `test_hubspot_kg_populated`
  - `test_hubspot_second_run_uses_kg`

**Dependencies**: None

---

### WS1-T2: Add GitHub Issues API (DevOps)
**Priority**: P1 | **Effort**: M (6-8h)

**Files to create/modify**:
| File | Nature of Change |
|------|------------------|
| `tests/fixtures/github_issues_openapi.yaml` | NEW — OpenAPI spec with 4 endpoints |
| `src/integration_coworker/graph/nodes/align_task_with_kg.py` | ADD — 3 templates |
| `tests/test_github_integration.py` | NEW — 6 tests |

**OpenAPI Structure** (demonstrates path params):
```yaml
paths:
  /repos/{owner}/{repo}/issues:
    post:  # CreateIssue
    get:   # ListIssues
  /repos/{owner}/{repo}/issues/{issue_number}:
    get:   # GetIssue
    patch: # UpdateIssue
```

**Why this matters**: Tests multiple path parameters (`{owner}`, `{repo}`, `{issue_number}`), which the current codegen may not handle well.

**Definition of Done**:
- [ ] Path parameters correctly extracted in Silver model
- [ ] Generated client code uses path params (e.g., `f"/repos/{owner}/{repo}/issues"`)
- [ ] 6 tests passing

**Dependencies**: None (can parallelize with WS1-T1)

---

### WS1-T3: Provider Inference from Spec
**Priority**: P2 | **Effort**: S (2-3h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/nodes/detect_and_parse_spec.py` | ENHANCE — Infer provider from `info.title` or `servers[0].url` |
| `tests/test_build_silver_api_model.py` | ADD — 3 tests for inference |

**Logic**:
```python
def infer_provider_code(spec: dict) -> str | None:
    # Strategy 1: Check servers[0].url
    servers = spec.get("servers", [])
    if servers:
        url = servers[0].get("url", "")
        if "stripe.com" in url: return "stripe"
        if "hubspot.com" in url: return "hubspot"
        if "github.com" in url: return "github"
    
    # Strategy 2: Check info.title
    title = spec.get("info", {}).get("title", "").lower()
    if "stripe" in title: return "stripe"
    if "hubspot" in title: return "hubspot"
    if "github" in title: return "github"
    
    return None  # Fall back to filename-based
```

**Definition of Done**:
- [ ] `--provider` flag is optional for known specs
- [ ] Test: `design_and_generate_integration(spec_refs=["stripe_openapi.yaml"], task="...")` infers `stripe`

**Dependencies**: WS1-T1, WS1-T2 (need more specs to test against)

---

## Workstream 2: KG/GraphRAG Maturity

**Goal**: KG should be the primary path, with full observability.

### WS2-T1: `kg-query` CLI Command
**Priority**: P1 | **Effort**: S (3-4h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/cli.py` | ADD — `kg-query` command |
| `src/integration_coworker/kg/__init__.py` | ENHANCE — Return score breakdown in `KGTemplateMatch` |

**Command**:
```bash
integration-coworker kg-query \
  --provider stripe \
  --task "Create a payment for a subscription"
```

**Output**:
```
Top 5 templates for 'Create a payment for a subscription':

1. template.stripe.create_payment_intent (score: 0.87)
   ├── Graph score:     0.35 (edges to endpoint, entity)
   ├── Embedding score: 0.45 (cosine similarity)
   └── Exact match:     0.07 (partial match on 'payment')

2. template.stripe.confirm_payment_intent (score: 0.62)
   ...
```

**Definition of Done**:
- [ ] `kg-query` command exists
- [ ] Shows top-k templates with score breakdown
- [ ] `--json` mode for scripting
- [ ] Test: `test_kg_query_cli.py` with 2 tests

**Dependencies**: None

---

### WS2-T2: Strict KG-First Test
**Priority**: P0 | **Effort**: S (1-2h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `tests/test_graphrag_integration.py` | ENHANCE — Add strict assertion |

**Current state**: `test_second_run_uses_kg_templates` passes but doesn't strictly assert KG was used vs fallback.

**Change**:
```python
def test_second_run_strictly_uses_kg(self):
    """Prove that second run uses KG template (NOT fallback)."""
    # ... first run ...
    
    # Second run
    result2 = design_and_generate_integration(...)
    
    templates = result2.plan.get("candidate_templates", [])
    assert len(templates) >= 1, "Should find KG template"
    
    # STRICT: template must have non-None template_id (proves it came from DB)
    assert templates[0].get("template_id") is not None, \
        "Template should have DB-assigned template_id (not in-memory fallback)"
    
    # STRICT: template_id should be a key like "template.mock_payments.create_checkout_session"
    assert templates[0]["template_id"].startswith("template."), \
        f"Template ID should be DB key format, got: {templates[0]['template_id']}"
```

**Definition of Done**:
- [ ] Test fails if in-memory fallback is used
- [ ] Test passes after proper KG population

**Dependencies**: None

---

### WS2-T3: KG Metrics in Report
**Priority**: P1 | **Effort**: S (2-3h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/state.py` | ADD — `kg_metrics: dict` field |
| `src/integration_coworker/graph/nodes/align_task_with_kg.py` | WRITE — Populate `state.kg_metrics` |
| `src/integration_coworker/graph/nodes/build_report.py` | ADD — "## KG Retrieval" section |

**`kg_metrics` structure**:
```python
kg_metrics = {
    "templates_considered": 5,
    "best_template": "template.stripe.create_payment_intent",
    "best_score": 0.87,
    "score_breakdown": {
        "graph_score": 0.35,
        "embedding_score": 0.45,
        "exact_match_bonus": 0.07,
    },
    "fallback_used": False,
}
```

**Report section**:
```markdown
## KG Retrieval

| Metric | Value |
|--------|-------|
| Templates considered | 5 |
| Best template | template.stripe.create_payment_intent |
| Best score | 0.87 |
| Fallback used | No |

Score breakdown:
- Graph score: 0.35
- Embedding score: 0.45
- Exact match: 0.07
```

**Definition of Done**:
- [ ] `state.kg_metrics` populated after `align_task_with_kg`
- [ ] Report includes KG section
- [ ] Test: `test_report_includes_kg_metrics`

**Dependencies**: None

---

### WS2-T4: Embedding Verification
**Priority**: P2 | **Effort**: M (4-6h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/kg/__init__.py` | VERIFY — Embedding path works with real API |
| `tests/test_graphrag_integration.py` | ADD — Test with `USE_MOCK_LLM=false` (skip if no key) |

**Issue**: Current tests all use `USE_MOCK_LLM=true`, which means embeddings are never computed. The `_compute_embedding` function returns `None` in mock mode, falling back to default 0.5 similarity.

**Change**:
```python
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="Real embeddings require API key")
def test_embedding_similarity_affects_ranking():
    """Test that embeddings actually influence ranking (requires real API)."""
    # Two runs with different but related tasks
    # Verify the second run ranks similar tasks higher
```

**Definition of Done**:
- [ ] Embeddings computed when API key present
- [ ] Embedding dimension matches pgvector schema (1536)
- [ ] Test skips gracefully without API key

**Dependencies**: None

---

## Workstream 3: Codegen Polish

**Goal**: Generated code works without manual fixes.

### WS3-T1: Fix Relative Import Problem
**Priority**: P0 | **Effort**: M (4-6h)

**Current Issue** (`test_m4_generated_code_execution.py:86-89`):
```python
# Fix import in generated flow file (temporary workaround)
content = content.replace("from .clients.mock_payments", "from integrations.clients.mock_payments")
```

**Root Cause**: `generate_code_and_tests.py` computes import paths assuming `PYTHONPATH=src`, but the import statement uses `.clients` (relative) which only works inside a package with `__init__.py`.

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/codegen/paths.py` | ENHANCE — `compute_import_path()` to handle non-package repos |
| `src/integration_coworker/graph/nodes/generate_code_and_tests.py` | USE — Always absolute imports by default |
| `tests/test_m4_generated_code_execution.py` | REMOVE — The workaround hack |

**Solution**:
1. Default to **absolute imports** (e.g., `from integrations.clients.mock_payments import ...`)
2. Only use relative imports if `RepoProfile.conventions["use_relative_imports"] = True`
3. Ensure `integrations_root` is correctly stripped from import path

**Change in `_generate_flow_code`**:
```python
# BEFORE (problematic)
client_import_module = path_to_module(strip_src_prefix(f"{clients_dir}/{client_module}.py"))
# e.g., "integrations.clients.mock_payments"

# Generated code uses:
# from {client_import_module} import {client_class}
# → from integrations.clients.mock_payments import MockPaymentsClient ✓
```

The issue is actually in how `client_import_module` is constructed. Let me trace it:
- `clients_dir` = `"src/integrations/clients"` (from profile)
- `strip_src_prefix("src/integrations/clients/mock_payments.py")` = `"integrations/clients/mock_payments.py"`
- `path_to_module(...)` = `"integrations.clients.mock_payments"` ✓

The code looks correct. The bug must be elsewhere. Need to check actual generated content.

**Definition of Done**:
- [ ] Remove the workaround from test
- [ ] Test still passes
- [ ] Generated imports work with `PYTHONPATH=src` (no relative)

**Dependencies**: None (blocking for M5 completion)

---

### WS3-T2: Post-Generation Import Validation
**Priority**: P1 | **Effort**: S (3-4h)

**Files to create/modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/nodes/validate_integration_design.py` | ENHANCE — Add import check |

**Logic**:
```python
def _validate_imports(code_artifacts: List[CodeArtifact], repo_root: Path) -> List[str]:
    """Try to import generated modules and return errors."""
    errors = []
    
    for artifact in code_artifacts:
        if artifact.language != "python":
            continue
        
        full_path = repo_root / artifact.rel_path
        if not full_path.exists():
            errors.append(f"File not written: {artifact.rel_path}")
            continue
        
        # Try to parse for syntax
        try:
            ast.parse(full_path.read_text())
        except SyntaxError as e:
            errors.append(f"Syntax error in {artifact.rel_path}: {e}")
    
    return errors
```

**Definition of Done**:
- [ ] Validation catches missing files
- [ ] Validation catches syntax errors
- [ ] Errors added to `state.errors` (but don't fail run)
- [ ] Test: `test_validate_catches_bad_import`

**Dependencies**: WS3-T1

---

### WS3-T3: More Repo Archetypes
**Priority**: P1 | **Effort**: M (4-5h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/repo/profiles.py` | ADD — Flask, Express, NestJS profiles + detection |
| `tests/test_repo_profiles.py` | ADD — 9 tests (3 per archetype) |

**Profiles to add**:
```python
FLASK_PROFILE = RepoProfile(
    name="flask",
    framework="flask",
    language="python",
    integrations_root="app/integrations",
    tests_root="tests",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "services/{provider}_{task}.py",
    }
)

EXPRESS_PROFILE = RepoProfile(
    name="express",
    framework="express",
    language="javascript",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.js",
        "flow_module_pattern": "services/{provider}-{task}.js",
    }
)

NESTJS_PROFILE = RepoProfile(
    name="nestjs",
    framework="nestjs",
    language="typescript",
    integrations_root="src/integrations",
    tests_root="test/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.service.ts",
        "flow_module_pattern": "services/{provider}-{task}.service.ts",
    }
)
```

**Detection heuristics**:
```python
# Flask: app.py with "from flask import" or requirements.txt with flask
# Express: package.json with "express" dependency
# NestJS: package.json with "@nestjs/core" dependency
```

**Definition of Done**:
- [ ] 3 new profiles exist
- [ ] Detection works for each
- [ ] 9 tests passing

**Dependencies**: None

---

## Workstream 4: Observability & Ergonomics

**Goal**: Make the system debuggable and demo-friendly.

### WS4-T1: `--verbose` CLI Flag
**Priority**: P2 | **Effort**: M (4-5h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/cli.py` | ADD — `--verbose` option to `run` and `demo` |
| `src/integration_coworker/graph/nodes/*.py` | ADD — Verbose logging at key decision points |

**What to log**:
```
[VERBOSE] detect_and_parse_spec: Detected format=openapi_3, endpoints=4
[VERBOSE] detect_profile_from_repo: Detected profile=fastapi from pyproject.toml
[VERBOSE] align_task_with_kg: KG query returned 3 templates
[VERBOSE] align_task_with_kg: Selected template=template.stripe.create_payment_intent (score=0.87)
[VERBOSE] generate_code_and_tests: Generated 3 artifacts (client, flow, test)
```

**Implementation**:
```python
@app.command("run")
def run_integration(
    ...,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed decision logs"),
):
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
        # Or: set env var that nodes check
```

**Definition of Done**:
- [ ] `--verbose` flag works
- [ ] Key decisions logged
- [ ] Not too noisy (max 20-30 lines for typical run)

**Dependencies**: None

---

### WS4-T2: Health Check Command
**Priority**: P2 | **Effort**: S (2-3h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/cli.py` | ADD — `health` command |

**Checks**:
```bash
$ integration-coworker health

Integration Co-Worker Health Check
==================================

Database:
  ✓ Connection: OK (SQLite)
  ✓ Schema: All tables exist
  ⚠ KG: Empty (0 templates)

LLM:
  ✓ Mode: Mock (USE_MOCK_LLM=true)

Embeddings:
  ⚠ Mode: Disabled (no API key)

Overall: HEALTHY (with warnings)
```

**Definition of Done**:
- [ ] `health` command exists
- [ ] Checks DB connection, schema, LLM config
- [ ] Returns exit code 0 if healthy, 1 if critical failures
- [ ] Test: `test_health_command`

**Dependencies**: None

---

### WS4-T3: LangSmith Documentation
**Priority**: P1 | **Effort**: S (2h)

**Files to create**:
| File | Nature of Change |
|------|------------------|
| `docs/LANGSMITH_SETUP.md` | NEW — Setup guide |
| `DEMO.md` | ENHANCE — Link to LangSmith docs |

**Content**:
```markdown
# LangSmith Integration

## Setup

1. Create account at smith.langchain.com
2. Create API key
3. Set environment variables:
   ```bash
   export LANGCHAIN_TRACING_V2=true
   export LANGCHAIN_API_KEY=lsv2_pt_...
   export LANGCHAIN_PROJECT=integration-coworker
   ```

## What's Traced

- LLM calls (codegen prompts + responses)
- Embedding requests
- Full run metadata (run_id, provider, task)

## Viewing Traces

Navigate to: https://smith.langchain.com/o/<org>/projects/integration-coworker
```

**Definition of Done**:
- [ ] `docs/LANGSMITH_SETUP.md` exists
- [ ] DEMO.md links to it
- [ ] Instructions verified against actual traces

**Dependencies**: None

---

### WS4-T4: Troubleshooting in DEMO.md
**Priority**: P2 | **Effort**: S (1-2h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `DEMO.md` | ADD — Troubleshooting section |

**Issues to cover**:
```markdown
## Troubleshooting

### "Demo spec not found"
Run from project root:
```bash
cd /path/to/solver-agentic-spec-coworker
integration-coworker demo
```

### "No KG templates found"
Run with `--persist` to populate KG:
```bash
integration-coworker demo --persist
integration-coworker demo --dry-run  # Now finds templates
```

### "psycopg not installed"
```bash
pip install 'psycopg[binary]' psycopg_pool
```

### "pgvector extension not found"
In Postgres:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```
```

**Definition of Done**:
- [ ] 5+ common issues documented
- [ ] Each has solution
- [ ] Linked from main troubleshooting section

**Dependencies**: None

---

## Week-by-Week Plan

### Week 1: Multi-Provider + Core Fixes
**Focus**: Prove generality, fix blocking import issue

| Day | Tasks | Deliverable |
|-----|-------|-------------|
| Mon | WS1-T1: HubSpot fixture + templates | `hubspot_contacts_openapi.yaml` |
| Tue | WS1-T1: HubSpot tests | 6 tests passing |
| Wed | WS3-T1: Debug import issue | Identify root cause |
| Thu | WS3-T1: Fix import generation | Remove test hack |
| Fri | WS2-T2: Strict KG test | Tighter assertion |

**End-of-Week Checkpoint**:
- [ ] HubSpot integration works e2e
- [ ] Import issue fixed (no more workaround)
- [ ] 165+ tests passing

---

### Week 2: GitHub + KG Observability
**Focus**: Another provider, KG metrics visible

| Day | Tasks | Deliverable |
|-----|-------|-------------|
| Mon | WS1-T2: GitHub fixture + templates | `github_issues_openapi.yaml` |
| Tue | WS1-T2: GitHub tests | 6 tests passing |
| Wed | WS2-T1: `kg-query` command | CLI works |
| Thu | WS2-T3: KG metrics in report | Report has KG section |
| Fri | WS3-T2: Import validation | Validation catches issues |

**End-of-Week Checkpoint**:
- [ ] 4 providers working (mock, stripe, hubspot, github)
- [ ] `kg-query` command shows score breakdown
- [ ] Report includes KG retrieval section
- [ ] 175+ tests passing

---

### Week 3: Profiles + Ergonomics
**Focus**: More archetypes, better UX

| Day | Tasks | Deliverable |
|-----|-------|-------------|
| Mon | WS3-T3: Flask profile + detection | 3 tests passing |
| Tue | WS3-T3: Express + NestJS profiles | 6 more tests passing |
| Wed | WS4-T1: `--verbose` flag | Verbose output works |
| Thu | WS4-T2: `health` command | Health check works |
| Fri | WS4-T3: LangSmith docs | `LANGSMITH_SETUP.md` |

**End-of-Week Checkpoint**:
- [ ] 6 repo archetypes (FastAPI, Django, Next.js, Flask, Express, NestJS)
- [ ] `--verbose` and `health` commands work
- [ ] LangSmith setup documented
- [ ] 180+ tests passing

---

### Week 4: Polish + Release
**Focus**: Documentation, edge cases, tagging

| Day | Tasks | Deliverable |
|-----|-------|-------------|
| Mon | WS4-T4: Troubleshooting in DEMO | DEMO.md updated |
| Tue | WS1-T3: Provider inference | Optional `--provider` |
| Wed | WS2-T4: Embedding verification | Skip-if-no-key test |
| Thu | Full regression, update CHANGELOG | 180+ tests pass |
| Fri | Tag `m5-complete`, update docs | Release ready |

**End-of-Week Checkpoint**:
- [ ] All P0/P1 items complete
- [ ] DEMO.md comprehensive
- [ ] CHANGELOG updated
- [ ] Tag `m5-complete` pushed

---

## Definition of Done for M5

| Criterion | Target | Measurement |
|-----------|--------|-------------|
| Providers | 4+ | mock, stripe, hubspot, github |
| Tests | 180+ | `pytest tests -v` |
| Archetypes | 6 | Detection tests pass |
| Import hack | Removed | `test_m4_generated_code_execution.py` clean |
| KG observability | Complete | `kg-query` + report metrics |
| Documentation | Updated | DEMO, LANGSMITH_SETUP, Troubleshooting |

---

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Import fix more complex than expected | Medium | High | Timebox to 1 day; if blocked, add as known limitation |
| Real embeddings break in CI | Low | Medium | Skip-if-no-key pattern already in place |
| HubSpot/GitHub spec shapes break parser | Low | Medium | Use minimal subset of spec; test incrementally |
| `--verbose` too noisy | Medium | Low | Use structured log levels; filter by node |

---

## Appendix: File Change Summary

| Workstream | New Files | Modified Files |
|------------|-----------|----------------|
| WS1: Multi-Provider | 4 (`hubspot_*.yaml`, `github_*.yaml`, 2 test files) | 1 (`align_task_with_kg.py`) |
| WS2: KG/GraphRAG | 0 | 4 (`cli.py`, `kg/__init__.py`, `state.py`, `build_report.py`) |
| WS3: Codegen | 0 | 3 (`paths.py`, `generate_code_and_tests.py`, `profiles.py`) |
| WS4: Observability | 1 (`LANGSMITH_SETUP.md`) | 2 (`cli.py`, `DEMO.md`) |

**Total**: 5 new files, 10 modified files

---

*Plan prepared by Staff Engineer for M5 Sprint*
