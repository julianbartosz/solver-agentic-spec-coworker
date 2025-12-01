# M5 Tactical Implementation Plan

**Document Version**: 4.0  
**Created**: November 28, 2025  
**Revised**: November 28, 2025 (Implementation Complete - P0 Items)  
**Engineer**: Single IC (4-week sprint)  
**Baseline**: `m4-demo-ready` tag (158 tests passing)  
**Current Status**: 168 tests passing  
**Audit Reference**: `docs/AGENTIC_ARCHITECTURE_AUDIT.md`

---

## Executive Summary

M5 focuses on **dynamic spec handling** — the system should work with **any valid OpenAPI spec**, not just pre-configured providers.

### Core Insight

The current `_LEGACY_WORKFLOW_TEMPLATES` dict is a **bootstrap artifact**, not the target architecture. It creates a false impression that we need to "add providers" when the system should handle arbitrary specs dynamically.

**The Real Flow**:
```
ANY OpenAPI Spec → Parse → Infer Provider → Query KG → Generic Fallback → Generate Code
                                              ↓
                                    (learns for next run)
```

### Four Workstreams:
1. **Dynamic Spec Handling** — Remove hardcoded deps, strengthen generic fallback ✅ P0 COMPLETE
2. **KG/GraphRAG Maturity** — Cross-provider learning, semantic pattern matching
3. **Codegen Polish** — Fix import paths, validate generated code runs ✅ COMPLETE
4. **Observability & Ergonomics** — Verbose mode, health checks, documentation

**Target**: 180+ tests, works with ANY OpenAPI spec, zero hardcoded provider assumptions.
**Current**: 168 tests passing, P0 items complete.

---

## ✅ Completed Work (Week 1)

### WS1-T1: Deprecate `_LEGACY_WORKFLOW_TEMPLATES` — ✅ DONE
- `USE_LEGACY_TEMPLATES` env var gate added (default: OFF)
- `_check_legacy_templates_enabled()` function in `align_task_with_kg.py`
- Legacy templates only used when explicitly enabled

### WS1-T2: Smarter Generic Fallback — ✅ DONE
- `_infer_workflow_from_endpoint()` function added
- HTTP method-aware workflow inference:
  - POST → 5-step create workflow (start, validate, call, transform, end)
  - GET (list) → 4-step list workflow
  - GET (single) → 4-step fetch workflow
  - PATCH → 5-step update workflow
  - DELETE → 4-step delete workflow
- Creates semantically meaningful node labels

### WS1-T3: Provider Inference from Spec — ✅ DONE
- `infer_provider_from_spec_content()` in `plan_run.py`
- Infers from `servers[0].url` domain first
- Falls back to `info.title` slug
- Hardcoded provider list removed

### WS3-T1: Fix Relative Import Problem — ✅ DONE
- Fixed IntegrationError import in `_generate_flow_code()`
- Now uses `integration_coworker.runtime.exceptions.IntegrationError`
- Removed workaround hack from `test_m4_generated_code_execution.py`

### WS1-T6: Unknown Spec Integration Tests — ✅ DONE
- `tests/fixtures/acme_widgets_openapi.yaml` created
- `tests/test_dynamic_spec.py` created with 10 tests:
  - `test_unknown_spec_works_without_provider_code`
  - `test_inferred_provider_matches_spec_title`
  - `test_known_spec_still_works_without_provider_code`
  - `test_workflow_inferred_from_http_method_post`
  - `test_workflow_inferred_from_http_method_get_list`
  - `test_workflow_inferred_from_http_method_delete`
  - `test_legacy_templates_disabled_by_default`
  - `test_legacy_templates_enabled_when_requested`
  - `test_generated_client_uses_correct_imports`
  - `test_generated_flow_uses_correct_imports`

---

## Architectural Clarification

### What "Provider" Actually Means

| Concept | Definition | Example |
|---------|------------|---------|
| **provider_code** | String identifier for an API source | `"stripe"`, `"my_internal_api"`, `"acme"` |
| **Spec** | OpenAPI document describing the API | Any valid OpenAPI 3.x YAML/JSON |
| **Workflow Template** | Reusable pattern (validate → call → transform) | Learned from successful runs |

### How It Should Work

```
┌─────────────────────────────────────────────────────────────────┐
│  User provides: spec URL/path + task description                │
│  (provider_code optional — inferred from spec)                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  1. Parse ANY valid OpenAPI spec                                │
│     → Extract endpoints, schemas, entities                      │
│     → Infer provider_code from info.title / servers[0].url     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. Query KG for matching patterns                              │
│                                                                 │
│     KG contains LEARNED patterns, not hardcoded ones:           │
│     - "POST to /*/items → create pattern"                       │
│     - "GET /*/items/{id} → fetch-by-id pattern"                │
│     - Semantic similarity to previous tasks                     │
│                                                                 │
│     IF no match: use GENERIC fallback (provider-agnostic)       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. Generate code from PARSED SPEC (not hardcoded templates)    │
│     → endpoint.path, endpoint.method come from spec             │
│     → client methods derived from spec operationIds             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. Learn patterns into KG for next run                         │
│     → "create_widget for acme" becomes a template               │
│     → Similar future tasks match via embeddings                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Current State Analysis

### What Works (M4)
| Component | Status | Notes |
|-----------|--------|-------|
| OpenAPI parsing | ✅ Dynamic | Parses any valid spec |
| Provider inference | ⚠️ Partial | Falls back to filename |
| Generic fallback | ✅ Works | 4-step workflow for unknown providers |
| KG population | ✅ Works | Learns from successful runs |
| KG retrieval | ⚠️ Limited | Only matches exact provider_code |

### The Problem: `_LEGACY_WORKFLOW_TEMPLATES`

```python
# align_task_with_kg.py — THIS IS THE BOOTSTRAP CRUTCH
_LEGACY_WORKFLOW_TEMPLATES = {
    ("stripe", "create_payment_intent"): { ... },
    ("mock_payments", "create_checkout_session"): { ... },
    # ❌ Hardcoded — doesn't scale
}
```

**Why it exists**: Demo/testing before KG is populated.  
**Why it's wrong**: Creates expectation that we "add providers" manually.

---

## Workstream 1: Dynamic Spec Handling

**Goal**: Remove hardcoded provider dependencies; any spec works on first run.

### WS1-T1: Deprecate `_LEGACY_WORKFLOW_TEMPLATES`
**Priority**: P0 | **Effort**: M (4-6h)

**Problem**: The legacy dict creates false dependency on "known providers".

**Solution**: 
1. Gate behind `USE_LEGACY_TEMPLATES=1` env var (default: off)
2. Strengthen generic fallback to be production-quality
3. Add tests proving unknown specs work

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/nodes/align_task_with_kg.py` | Gate legacy dict behind env var |
| `tests/test_dynamic_spec.py` | NEW — Tests for arbitrary specs |

**New test cases**:
```python
def test_unknown_provider_uses_generic_fallback():
    """Spec we've never seen before still generates valid code."""
    result = design_and_generate_integration(
        spec_refs=["tests/fixtures/acme_widgets_openapi.yaml"],
        task_description="Create a widget",
        # NO provider_code — should be inferred
    )
    assert result.code_artifacts  # Generated code
    assert result.task.provider_code == "acme"  # Inferred

def test_generic_fallback_workflow_structure():
    """Generic fallback produces valid workflow."""
    # With empty KG and no legacy fallback
    result = design_and_generate_integration(...)
    
    # Should have 4-step generic workflow
    assert len(result.workflow_nodes) == 4
    assert result.workflow_nodes[0].node_type == "start"
    assert result.workflow_nodes[1].node_type == "validation"
    assert result.workflow_nodes[2].node_type == "api_call"
    assert result.workflow_nodes[3].node_type == "end"
```

**Definition of Done**:
- [ ] `USE_LEGACY_TEMPLATES=0` is default
- [ ] Tests pass without legacy templates
- [ ] New `test_dynamic_spec.py` with 5+ tests
- [ ] Existing tests still pass (backwards compat)

---

### WS1-T2: Smarter Generic Fallback
**Priority**: P0 | **Effort**: M (6-8h)

**Problem**: Current generic fallback is too simple — just 4 steps regardless of HTTP method or response type.

**Solution**: Infer workflow pattern from spec structure:

| Spec Pattern | Inferred Workflow |
|--------------|-------------------|
| `POST /items` with request body | validate_input → create_resource → return_created |
| `GET /items/{id}` | validate_id → fetch_resource → return_or_404 |
| `GET /items` with query params | build_query → list_resources → paginate_response |
| `PATCH /items/{id}` | validate_input → fetch_existing → update_resource |
| `DELETE /items/{id}` | validate_id → delete_resource → confirm_deleted |

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/nodes/align_task_with_kg.py` | NEW — `_infer_workflow_from_spec()` |
| `tests/test_workflow_inference.py` | NEW — Tests for each HTTP method pattern |

**Implementation**:
```python
def _infer_workflow_from_spec(
    endpoint: Endpoint,
    task_description: str,
) -> List[dict]:
    """Infer workflow steps from endpoint structure (no KG needed)."""
    
    steps = [{"key": "start", "type": "start", "label": "Start"}]
    
    # Validation step — always present
    if endpoint.request_body or endpoint.path_params:
        steps.append({
            "key": "validate_input",
            "type": "validation",
            "label": "Validate Input",
            "description": _describe_validation(endpoint),
        })
    
    # API call step — method-specific
    steps.append({
        "key": "call_api",
        "type": "api_call",
        "label": f"{endpoint.method.upper()} {endpoint.path}",
        "description": endpoint.summary or "",
    })
    
    # Transform step — based on response type
    if endpoint.method.upper() == "GET" and _is_list_response(endpoint):
        steps.append({
            "key": "paginate",
            "type": "transform",
            "label": "Handle Pagination",
        })
    elif endpoint.method.upper() in ("POST", "PUT", "PATCH"):
        steps.append({
            "key": "transform_response",
            "type": "transform", 
            "label": "Extract Created/Updated Resource",
        })
    
    steps.append({"key": "end", "type": "end", "label": "Return Result"})
    
    return steps
```

**Definition of Done**:
- [ ] `_infer_workflow_from_spec()` handles GET/POST/PUT/PATCH/DELETE
- [ ] Inferred workflows are more specific than generic 4-step
- [ ] Tests for each HTTP method pattern

---

### WS1-T3: Provider Inference from Spec
**Priority**: P0 | **Effort**: S (2-3h)

**Problem**: `plan_run.py:42-53` has hardcoded provider detection:
```python
if "stripe" in primary_ref.lower():
    state.provider_code = "stripe"
elif "mock_payments" in primary_ref.lower():
    state.provider_code = "mock_payments"  # ❌ Hardcoded list
```

**Solution**: Infer from spec content instead:

```python
def infer_provider_code(spec: dict, spec_path: str) -> str:
    """Infer provider_code from spec content."""
    
    # Strategy 1: servers[0].url domain
    servers = spec.get("servers", [])
    if servers:
        url = servers[0].get("url", "")
        domain = _extract_domain(url)
        if domain:
            return _domain_to_provider(domain)
            # "api.stripe.com" → "stripe"
            # "api.acme.io" → "acme"
    
    # Strategy 2: info.title slug
    title = spec.get("info", {}).get("title", "")
    if title:
        return _slugify(title)
        # "Acme Widget API" → "acme_widget"
    
    # Strategy 3: filename
    return Path(spec_path).stem.replace("_openapi", "").replace("-", "_")
```

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/nodes/detect_and_parse_spec.py` | ADD — `infer_provider_code()` |
| `tests/test_provider_inference.py` | NEW — Inference tests |

**Definition of Done**:
- [ ] `--provider` flag is optional for all specs
- [ ] Inference works from servers URL, info.title, or filename
- [ ] Test with 5+ different spec formats

---

### WS1-T4: Test with Diverse Public Specs
**Priority**: P1 | **Effort**: M (4-6h)

**Goal**: Prove system works with real-world specs, not just our fixtures.

**Approach**: Download minimal subsets of public OpenAPI specs and test e2e.

**Files to create**:
| File | Description |
|------|-------------|
| `tests/fixtures/petstore_subset.yaml` | Classic Swagger example (pets CRUD) |
| `tests/fixtures/jsonplaceholder_openapi.yaml` | Simple REST API (posts, comments) |
| `tests/fixtures/openweather_subset.yaml` | Weather API (different shape) |
| `tests/test_public_specs.py` | E2E tests with these specs |

**Test structure**:
```python
@pytest.mark.parametrize("spec_file,task,expected_endpoint", [
    ("petstore_subset.yaml", "Create a pet", "POST /pets"),
    ("petstore_subset.yaml", "List all pets", "GET /pets"),
    ("jsonplaceholder_openapi.yaml", "Create a post", "POST /posts"),
    ("openweather_subset.yaml", "Get current weather", "GET /weather"),
])
def test_diverse_specs_generate_code(spec_file, task, expected_endpoint):
    """Diverse public specs all generate valid code."""
    result = design_and_generate_integration(
        spec_refs=[f"tests/fixtures/{spec_file}"],
        task_description=task,
    )
    
    assert result.code_artifacts
    assert expected_endpoint in str(result.code_artifacts[0].content)
```

**Definition of Done**:
- [ ] 3+ public spec subsets as fixtures
- [ ] All generate valid code without hardcoded templates
- [ ] Parametrized tests prove generality

---

### WS1-T5: Dynamic Policy Inference from Spec
**Priority**: P1 | **Effort**: M (4-5h)

**Problem**: `attach_policies_and_patterns.py` always adds the same 5 policies regardless of spec:
```python
# Current: hardcoded policies
auth_policy = Policy(... type="bearer" ...)  # ❌ What if it's API Key?
rate_limit_policy = Policy(... requests_per_second=10 ...)  # ❌ Arbitrary
```

**Solution**: Infer from OpenAPI `securitySchemes` and extension fields:

```python
def _infer_auth_policy(spec: dict) -> Policy:
    """Infer auth type from spec security schemes."""
    security = spec.get("components", {}).get("securitySchemes", {})
    
    for name, scheme in security.items():
        if scheme.get("type") == "http" and scheme.get("scheme") == "bearer":
            return Policy(type="bearer", ...)
        elif scheme.get("type") == "apiKey":
            return Policy(
                type="api_key",
                header=scheme.get("name"),
                location=scheme.get("in"),  # "header" or "query"
            )
        elif scheme.get("type") == "oauth2":
            return Policy(type="oauth2", flows=scheme.get("flows"), ...)
    
    return Policy(type="none")  # No auth required
```

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/nodes/attach_policies_and_patterns.py` | REFACTOR — Dynamic policy inference |
| `tests/test_policy_inference.py` | NEW — Test auth type detection |

**Definition of Done**:
- [ ] Auth type inferred from `securitySchemes`
- [ ] API Key location (`header` vs `query`) respected
- [ ] Tests for bearer, apiKey, oauth2, none

---

### WS1-T6: Unknown Spec Integration Tests
**Priority**: P1 | **Effort**: S (2-3h)

**Problem**: All tests explicitly pass `provider_code="mock_payments"`:
```python
# tests/test_end_to_end_integration.py
result = design_and_generate_integration(
    spec_refs=[mock_payments_spec],
    task_description="Create a checkout session",
    provider_code="mock_payments",  # ❌ Tests don't prove inference works
)
```

**Solution**: Add tests that DON'T specify `provider_code`:

```python
# tests/test_dynamic_spec.py
def test_unknown_spec_works_without_provider():
    """Completely new spec works without provider_code."""
    result = design_and_generate_integration(
        spec_refs=["tests/fixtures/acme_widgets.yaml"],
        task_description="Create a widget",
        # NO provider_code — should be inferred
    )
    assert result.task.provider_code  # Inferred something
    assert result.code_artifacts  # Generated code

def test_inferred_provider_matches_spec_title():
    """Provider code comes from spec info.title."""
    # Spec has info.title: "Acme Widget API"
    result = design_and_generate_integration(
        spec_refs=["tests/fixtures/acme_widgets.yaml"],
        task_description="List widgets",
    )
    assert result.task.provider_code in ("acme", "acme_widget", "acme_widget_api")
```

**Files to create**:
| File | Description |
|------|-------------|
| `tests/fixtures/acme_widgets.yaml` | Fake spec with distinctive title/servers |
| `tests/test_dynamic_spec.py` | Provider-less integration tests |

**Definition of Done**:
- [ ] 5+ tests that don't pass `provider_code`
- [ ] All still generate valid code
- [ ] Provider correctly inferred from spec

---

## Workstream 2: KG/GraphRAG Maturity

**Goal**: KG enables cross-provider learning, not just exact matching.

### WS2-T1: Cross-Provider Pattern Matching
**Priority**: P0 | **Effort**: M (6-8h)

**Problem**: Current KG query filters by exact `provider_code`. A "create" pattern learned from Stripe doesn't help with HubSpot.

**Solution**: Add provider-agnostic pattern nodes to KG:

```
Current KG Structure:
  template.stripe.create_payment → (provider: stripe)
  
Improved KG Structure:
  template.stripe.create_payment → (provider: stripe)
       ↓ instance_of
  pattern.create_resource → (provider: null, method: POST, has_request_body: true)
```

**Query flow**:
1. Try exact match: `provider=X, task=Y`
2. If no match, try pattern match: `method=POST, task contains "create"`
3. Return pattern with provider-agnostic steps

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/kg/__init__.py` | ADD — Pattern-level queries |
| `src/integration_coworker/graph/nodes/persist_kg_learning.py` | ADD — Store patterns alongside templates |
| `tests/test_cross_provider_learning.py` | NEW — Cross-provider tests |

**Test case**:
```python
def test_pattern_learned_from_stripe_helps_hubspot():
    """Pattern from one provider helps similar task on another."""
    # Run 1: Learn from Stripe
    result1 = design_and_generate_integration(
        spec_refs=["stripe_openapi.yaml"],
        task_description="Create a payment",
        options=IntegrationOptions(dry_run=False),
    )
    
    # Run 2: New provider, similar task
    result2 = design_and_generate_integration(
        spec_refs=["hubspot_openapi.yaml"],
        task_description="Create a contact",  # Similar "create X" pattern
        options=IntegrationOptions(dry_run=False),
    )
    
    # Should find pattern match, not just generic fallback
    templates = result2.plan.get("candidate_templates", [])
    assert len(templates) >= 1
    assert "create" in templates[0].get("name", "").lower()
```

**Definition of Done**:
- [ ] KG stores provider-agnostic patterns
- [ ] Query falls back to pattern match when exact match fails
- [ ] Cross-provider test passes

---

### WS2-T2: Semantic Task Matching
**Priority**: P1 | **Effort**: M (4-6h)

**Problem**: Task matching is keyword-based. "Create a checkout session" and "Initialize payment flow" don't match.

**Solution**: Use embeddings for semantic similarity:

```python
def query_workflow_templates(...):
    # Current: Exact/keyword match on task_slug
    # Improved: Embed task_description, compare to stored embeddings
    
    task_embedding = embed(task_description)
    
    # Query KG for templates with similar embeddings
    templates = db.query("""
        SELECT * FROM kg_nodes 
        WHERE node_type = 'workflow_template'
        ORDER BY embedding <-> %s  -- pgvector similarity
        LIMIT 5
    """, [task_embedding])
```

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/kg/__init__.py` | ENHANCE — Use embedding similarity |
| `src/integration_coworker/graph/nodes/persist_kg_learning.py` | ENSURE — Task embeddings are stored |

**Definition of Done**:
- [ ] Semantically similar tasks match (even with different wording)
- [ ] Works in mock LLM mode (graceful degradation)
- [ ] Test: "Create payment" matches template learned from "Initialize checkout"

---

### WS2-T3: KG Metrics in Report
**Priority**: P1 | **Effort**: S (2-3h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/state.py` | ADD — `kg_metrics: dict` field |
| `src/integration_coworker/graph/nodes/align_task_with_kg.py` | WRITE — Populate `state.kg_metrics` |
| `src/integration_coworker/graph/nodes/build_report.py` | ADD — "## Template Selection" section |

**Report section**:
```markdown
## Template Selection

| Source | Template | Score |
|--------|----------|-------|
| KG (exact) | — | No match |
| KG (pattern) | pattern.create_resource | 0.72 |
| Spec inference | POST /items → create pattern | — |

**Selected**: pattern.create_resource (from KG)
```

**Definition of Done**:
- [ ] Report shows where template came from
- [ ] Distinguishes: KG exact / KG pattern / spec inference / generic fallback

---

### WS2-T4: `kg-query` CLI Command
**Priority**: P1 | **Effort**: S (3-4h)

**Command**:
```bash
integration-coworker kg-query \
  --task "Create a payment for a subscription"
  # Note: No --provider required!
```

**Output**:
```
Template matches for: "Create a payment for a subscription"

By Pattern (cross-provider):
  1. pattern.create_resource (score: 0.85)
     └── Learned from: stripe.create_payment, mock.create_checkout
  
By Provider (if KG has this provider):
  (none — provider not in KG yet)

Spec Inference (if you provide --spec):
  POST endpoint detected → would use "create" pattern
```

**Definition of Done**:
- [ ] Works without `--provider` flag
- [ ] Shows pattern matches across providers
- [ ] `--json` mode for scripting

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

**Root Cause**: Generated code uses relative imports that require package structure.

**Solution**: Always use absolute imports based on `integrations_root`:

```python
# BEFORE (problematic)
from .clients.mock_payments import MockPaymentsClient

# AFTER (works with PYTHONPATH=src)
from integrations.clients.mock_payments import MockPaymentsClient
```

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/nodes/generate_code_and_tests.py` | FIX — Use absolute imports |
| `tests/test_m4_generated_code_execution.py` | REMOVE — The workaround hack |

**Definition of Done**:
- [ ] Remove workaround from test
- [ ] Test still passes
- [ ] Generated imports work with `PYTHONPATH=src`

---

### WS3-T2: Post-Generation Syntax Validation
**Priority**: P1 | **Effort**: S (3-4h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/graph/nodes/validate_integration_design.py` | ENHANCE — AST parse generated code |

**Logic**:
```python
def _validate_generated_code(artifacts: List[CodeArtifact]) -> List[str]:
    errors = []
    for artifact in artifacts:
        if artifact.language == "python":
            try:
                ast.parse(artifact.content)
            except SyntaxError as e:
                errors.append(f"Syntax error in {artifact.rel_path}: {e}")
    return errors
```

**Definition of Done**:
- [ ] Syntax errors caught before returning result
- [ ] Errors in `state.errors` with clear messages

---

### WS3-T3: More Repo Archetypes
**Priority**: P1 | **Effort**: M (4-5h)

**Files to modify**:
| File | Nature of Change |
|------|------------------|
| `src/integration_coworker/repo/profiles.py` | ADD — Flask, Express, NestJS profiles |
| `tests/test_repo_profiles.py` | ADD — 9 tests (3 per archetype) |

**Detection heuristics**:
```python
# Flask: app.py + "flask" in requirements
# Express: package.json + "express" in dependencies  
# NestJS: package.json + "@nestjs/core" in dependencies
```

**Definition of Done**:
- [ ] 6 total archetypes (current 3 + Flask, Express, NestJS)
- [ ] Detection tests pass

---

## Workstream 4: Observability & Ergonomics

### WS4-T1: `--verbose` CLI Flag
**Priority**: P2 | **Effort**: M (4-5h)

**What to log**:
```
[detect_and_parse_spec] Parsed spec: 4 endpoints, provider=acme (inferred from title)
[align_task_with_kg] KG query: 0 exact matches, 1 pattern match (pattern.create_resource)
[align_task_with_kg] Selected: pattern.create_resource (score: 0.72)
[generate_code_and_tests] Generated 3 artifacts: client, flow, test
```

**Definition of Done**:
- [ ] `--verbose` flag works
- [ ] Key decisions logged
- [ ] Not too noisy (~20-30 lines for typical run)

---

### WS4-T2: Health Check Command
**Priority**: P2 | **Effort**: S (2-3h)

```bash
$ integration-coworker health

Database:     ✓ SQLite connected
KG Status:    ⚠ Empty (0 templates, 0 patterns)
LLM:          ✓ Mock mode (USE_MOCK_LLM=true)
Embeddings:   ⚠ Disabled (no API key)

Recommendation: Run `integration-coworker demo --persist` to populate KG
```

---

### WS4-T3: LangSmith Documentation
**Priority**: P1 | **Effort**: S (2h)

**Files to create**:
- `docs/LANGSMITH_SETUP.md`

---

### WS4-T4: Troubleshooting in DEMO.md
**Priority**: P2 | **Effort**: S (1-2h)

**Issues to document**:
- "No templates found" → Expected on first run, KG learns
- "Import error in generated code" → Check PYTHONPATH
- "Provider not recognized" → It's inferred, not hardcoded

---

## Week-by-Week Plan

### Week 1: Dynamic Foundation
**Focus**: Remove hardcoded deps, prove arbitrary specs work

| Day | Tasks | Deliverable |
|-----|-------|-------------|
| Mon | WS1-T1: Deprecate legacy templates | Env var gate |
| Tue | WS1-T2: Smarter generic fallback | HTTP-method-aware inference |
| Wed | WS1-T2: Continue + tests | 5+ inference tests |
| Thu | WS3-T1: Fix import issue | Remove test hack |
| Fri | WS1-T3: Provider inference (remove hardcoded list) | Spec-based inference |

**Checkpoint**:
- [ ] `USE_LEGACY_TEMPLATES=0` by default
- [ ] Hardcoded provider list in `plan_run.py` removed
- [ ] Unknown specs generate valid code
- [ ] Import issue fixed
- [ ] 165+ tests passing

---

### Week 2: Cross-Provider Learning + Policy Inference
**Focus**: KG patterns transfer between providers, spec-aware policies

| Day | Tasks | Deliverable |
|-----|-------|-------------|
| Mon | WS2-T1: Pattern-level KG nodes | Schema + persist |
| Tue | WS2-T1: Pattern queries | Query falls back to patterns |
| Wed | WS1-T5: Dynamic policy inference | Auth from securitySchemes |
| Thu | WS1-T4: Public spec tests | 3 diverse fixtures |
| Fri | WS2-T3: KG metrics in report + WS1-T6 | Template source visible, unknown spec tests |

**Checkpoint**:
- [ ] Cross-provider pattern matching works
- [ ] Auth policy inferred from spec (not hardcoded)
- [ ] 3 public spec fixtures passing
- [ ] Report shows template source
- [ ] Tests exist that DON'T pass provider_code
- [ ] 175+ tests passing

---

### Week 3: Polish & Archetypes
**Focus**: More profiles, better UX, semantic matching

| Day | Tasks | Deliverable |
|-----|-------|-------------|
| Mon | WS3-T3: Flask + Express profiles | 6 tests |
| Tue | WS3-T3: NestJS profile | 3 more tests |
| Wed | WS3-T2: Syntax validation + WS2-T2: Semantic matching | Catches errors, embedding similarity |
| Thu | WS2-T4: `kg-query` command | Works without --provider |
| Fri | WS4-T1: `--verbose` flag | Decision logging |

**Checkpoint**:
- [ ] 6 repo archetypes
- [ ] `kg-query` works for pattern discovery
- [ ] Verbose output helpful
- [ ] 180+ tests passing

---

### Week 4: Documentation & Release
**Focus**: Docs, edge cases, tagging

| Day | Tasks | Deliverable |
|-----|-------|-------------|
| Mon | WS4-T2: Health check | `health` command |
| Tue | WS4-T3: LangSmith docs | `LANGSMITH_SETUP.md` |
| Wed | WS4-T4: Troubleshooting | DEMO.md updated |
| Thu | Full regression, CHANGELOG | All tests pass |
| Fri | Tag `m5-complete` | Release ready |

**Checkpoint**:
- [ ] All P0/P1 items complete
- [ ] Documentation comprehensive
- [ ] Tag pushed

---

## Definition of Done for M5

| Criterion | Target | Measurement |
|-----------|--------|-------------|
| Arbitrary specs | Work without hardcoded templates | `test_dynamic_spec.py` passes |
| Legacy templates | Deprecated (env var gated) | Default OFF |
| Hardcoded provider list | Removed from `plan_run.py` | Inference from spec content |
| Cross-provider learning | Patterns transfer | `test_cross_provider_learning.py` |
| Dynamic policies | Inferred from `securitySchemes` | `test_policy_inference.py` |
| Unknown spec tests | Don't pass `provider_code` | 5+ tests in `test_dynamic_spec.py` |
| Tests | 180+ | `pytest tests -v` |
| Archetypes | 6 | Detection tests pass |
| Import hack | Removed | Test is clean |
| KG observability | Template source in report | Visible in output |

---

## Audit Findings Reference

This plan incorporates findings from `docs/AGENTIC_ARCHITECTURE_AUDIT.md`:

| # | Anti-Pattern | Location | Fix Task |
|---|--------------|----------|----------|
| 1 | `_LEGACY_WORKFLOW_TEMPLATES` | `align_task_with_kg.py` | WS1-T1 |
| 2 | Hardcoded provider detection | `plan_run.py:42-53` | WS1-T3 |
| 3 | Static policy attachment | `attach_policies_and_patterns.py` | WS1-T5 |
| 4 | Import path hack | `test_m4_generated_code_execution.py` | WS3-T1 |
| 5 | Tests require `provider_code` | All `tests/*.py` | WS1-T6 |

---

## Key Insight Summary

| Before M5 | After M5 |
|-----------|----------|
| "Add HubSpot to provider list" | "Any spec works, patterns learned" |
| Hardcoded `_LEGACY_WORKFLOW_TEMPLATES` | Dynamic inference from spec structure |
| Provider-specific KG queries | Cross-provider pattern matching |
| "4 providers" metric | "Any OpenAPI spec" metric |

The system becomes **truly agentic** — it learns from experience and generalizes, rather than requiring manual configuration for each new API.

---

*Plan revised November 28, 2025 — Dynamic Spec Architecture*
