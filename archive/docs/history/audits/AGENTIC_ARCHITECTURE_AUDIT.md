# Agentic Architecture Audit: Hardcoded Workarounds

**Date**: November 28, 2025  
**Purpose**: Identify all places where hardcoding prevents truly dynamic/agentic behavior

---

## Summary

The audit found **7 major areas** where the system has hardcoded behaviors that prevent it from being truly dynamic/agentic:

| # | Area | Severity | Location | Fix Complexity |
|---|------|----------|----------|----------------|
| 1 | Workflow Templates | 🔴 Critical | `align_task_with_kg.py` | M |
| 2 | Provider Inference | 🟡 Medium | `plan_run.py` | S |
| 3 | Task Understanding | 🟢 Low | `understand_task.py` | Already OK |
| 4 | Policy Attachment | 🟡 Medium | `attach_policies_and_patterns.py` | M |
| 5 | Import Path Hack | 🔴 Critical | `test_m4_generated_code_execution.py` | S |
| 6 | Repo Profile Detection | 🟡 Medium | `profiles.py` | S |
| 7 | Test Suite Design | 🟡 Medium | `tests/*.py` | M |

---

## 1. 🔴 Workflow Templates (Critical)

### Location
`src/integration_coworker/graph/nodes/align_task_with_kg.py`

### Problem
```python
_LEGACY_WORKFLOW_TEMPLATES = {
    ("stripe", "create_payment_intent"): { ... },
    ("stripe", "confirm_payment_intent"): { ... },
    ("mock_payments", "create_checkout_session"): { ... },
    # ❌ Must add each provider+task combo manually
}
```

### Impact
- System appears to "not know" new providers
- Creates false expectation that providers must be pre-configured
- Prevents learning from successful runs

### Fix
1. Gate behind `USE_LEGACY_TEMPLATES=0` (default: off)
2. Strengthen generic fallback based on HTTP method
3. Let KG learning populate templates dynamically

---

## 2. 🟡 Provider Inference (Medium)

### Location
`src/integration_coworker/graph/nodes/plan_run.py:42-53`

### Problem
```python
if "stripe" in primary_ref.lower():
    state.provider_code = "stripe"
elif "mock_payments" in primary_ref.lower():
    state.provider_code = "mock_payments"
elif "github" in primary_ref.lower():
    state.provider_code = "github"
elif "petstore" in primary_ref.lower():
    state.provider_code = "petstore"
else:
    # ❌ Hardcoded list of known providers
```

### Impact
- Unknown filenames fall through to weak URL parsing
- No inference from spec content (`info.title`, `servers[0].url`)
- Creates impression that providers must be "registered"

### Fix
```python
def infer_provider_code(spec: dict, spec_path: str) -> str:
    # 1. Check spec servers[0].url
    if "stripe.com" in servers_url: return "stripe"
    
    # 2. Check spec info.title
    title = spec.get("info", {}).get("title", "")
    return slugify(title)  # "Acme Widget API" → "acme_widget"
    
    # 3. Filename fallback
    return Path(spec_path).stem
```

---

## 3. 🟢 Task Understanding (Already Dynamic)

### Location
`src/integration_coworker/graph/nodes/understand_task.py`

### Status: ✅ GOOD

The `_fallback_heuristic_understanding()` function is actually **properly dynamic**:
- Extracts action words (create, update, delete, get) from task description
- Extracts resource words (checkout, session, payment, customer) dynamically
- Matches against known entities from the parsed spec
- No hardcoded provider logic

**No fix needed** — this is the correct pattern to follow elsewhere.

---

## 4. 🟡 Policy Attachment (Medium)

### Location
`src/integration_coworker/graph/nodes/attach_policies_and_patterns.py`

### Problem
```python
# ALWAYS adds these policies - not spec-driven
auth_policy = Policy(... type="bearer" ...)
retry_policy = Policy(... retryable_status_codes=[429, 500, ...] ...)
idempotency_policy = Policy(... header_name="Idempotency-Key" ...)
rate_limit_policy = Policy(... requests_per_second=10 ...)
```

### Impact
- Same policies for all APIs regardless of spec
- Doesn't check if API uses Bearer auth vs API Key vs OAuth
- Doesn't respect rate limits from spec (`x-ratelimit-*` headers)
- Hardcoded idempotency header name

### Fix (Should Be)
```python
def _infer_auth_policy(endpoint: Endpoint, spec: dict) -> Policy:
    """Infer auth type from spec security schemes."""
    security = spec.get("components", {}).get("securitySchemes", {})
    if "bearerAuth" in security:
        return Policy(type="bearer", ...)
    elif "apiKeyAuth" in security:
        api_key_scheme = security["apiKeyAuth"]
        return Policy(type="api_key", header=api_key_scheme.get("name"), ...)
    # etc.

def _infer_rate_limit(spec: dict) -> Policy:
    """Check for x-ratelimit-limit in spec or use sensible default."""
    # Some specs include rate limit info
    ...
```

---

## 5. 🔴 Import Path Hack (Critical)

### Location
`tests/test_m4_generated_code_execution.py:86-89`

### Problem
```python
# Fix import in generated flow file (temporary workaround)
content = content.replace(
    "from .clients.mock_payments", 
    "from integrations.clients.mock_payments"
)
```

### Impact
- Generated code doesn't work without manual fixup
- Test hides the real bug in code generation
- Users would hit this immediately in real usage

### Root Cause
`generate_code_and_tests.py` uses wrong import style

### Fix
In `generate_code_and_tests.py`, always use absolute imports:
```python
# Use this:
from integrations.clients.mock_payments import MockPaymentsClient

# Not this:
from .clients.mock_payments import MockPaymentsClient
```

---

## 6. 🟡 Repo Profile Detection (Medium)

### Location
`src/integration_coworker/repo/profiles.py:200-205`

### Problem
```python
# Default fallback
return SUBATOMIC_MOCK_PROFILE  # ❌ Always returns this specific mock profile
```

### Impact
- Unknown repo structures get "subatomic mock" profile
- Should return a truly generic profile, not a named mock

### Also
The detection logic is provider-agnostic (✅ good), but:
- Only 3 archetypes detected (FastAPI, Django, Next.js)
- Missing: Flask, Express, NestJS, plain Python, etc.

### Fix
1. Rename `SUBATOMIC_MOCK_PROFILE` → `GENERIC_PYTHON_PROFILE`
2. Add more archetype detection
3. Make default profile truly generic (not tied to "subatomic")

---

## 7. 🟡 Test Suite Design (Medium)

### Location
All test files in `tests/`

### Problem
Tests explicitly pass `provider_code="mock_payments"` or `provider_code="stripe"`:

```python
# tests/test_end_to_end_integration.py
result = design_and_generate_integration(
    spec_refs=[mock_payments_spec],
    task_description="Create a checkout session",
    provider_code="mock_payments",  # ❌ Hardcoded
)
```

### Impact
- Tests don't prove provider inference works
- No tests for "completely unknown" specs
- Reinforces impression that provider must be specified

### Fix
Add tests that DON'T specify provider_code:
```python
def test_unknown_spec_works_without_provider():
    """Completely new spec works without provider_code."""
    result = design_and_generate_integration(
        spec_refs=["tests/fixtures/acme_widgets.yaml"],
        task_description="Create a widget",
        # NO provider_code — should be inferred
    )
    assert result.task.provider_code  # Inferred something
    assert result.code_artifacts  # Generated code
```

---

## Pattern Analysis: What's Dynamic vs Hardcoded

### ✅ Already Dynamic (Good Patterns to Follow)
| Component | Why It's Good |
|-----------|---------------|
| OpenAPI parsing | Parses any valid spec |
| Entity extraction | Derives from spec schemas |
| Endpoint extraction | Derives from spec paths |
| Task understanding heuristics | Keyword-based, not provider-specific |
| KG learning | Learns from any successful run |
| Code generation structure | Uses parsed endpoint info |
| Codegen naming | Derives from provider_code + task_slug |

### ❌ Hardcoded (Need Fixes)
| Component | What's Hardcoded |
|-----------|------------------|
| `_LEGACY_WORKFLOW_TEMPLATES` | Provider+task → template mapping |
| Provider inference in `plan_run.py` | List of known provider names |
| Policy attachment | Same policies for all APIs |
| Test assertions | Specific provider names |
| Default repo profile | Named "subatomic mock" |

---

## Priority Order for Fixes

### P0 (Before M5 Completion)
1. **Gate legacy templates** — `USE_LEGACY_TEMPLATES=0` default
2. **Fix import paths** — Remove test workaround
3. **Provider inference from spec** — Use `info.title` and `servers[0].url`

### P1 (M5 Week 2-3)
4. **Smarter generic fallback** — HTTP method → workflow pattern
5. **Cross-provider patterns in KG** — Pattern nodes for learning transfer
6. **Dynamic policy inference** — Read auth from spec `securitySchemes`

### P2 (M5 Week 4 or later)
7. **More repo archetypes** — Flask, Express, NestJS
8. **Test suite refactor** — Add "unknown provider" tests
9. **Rename mock profile** — `GENERIC_PYTHON_PROFILE`

---

## 🎯 VERIFICATION RESULTS (November 29, 2025)

### Multi-Spec Support: ✅ CONFIRMED WORKING

**Test Results:**
- `test_multi_spec.py`: **6/6 tests passing**
- Multiple specs are correctly ingested, parsed, and combined

**Verified Behaviors:**
```
Multi-Spec Ingestion:
  - Spec documents ingested: 2
    - tests/fixtures/mock_payments_openapi.yaml
    - tests/fixtures/mock_notifications_openapi.yaml
  - Total endpoints from all specs: 4
    - POST /v1/checkout/sessions
    - GET /v1/checkout/sessions/{id}
    - POST /v1/notifications
    - GET /v1/notifications/{id}
  - Total schemas from all specs: 4
  - Provider (from primary): mock_payments
```

### Dynamic/Agentic Behavior: ✅ CONFIRMED WORKING

**Test Results:**
- `test_dynamic_spec.py`: **10/10 tests passing**
- `test_public_specs.py`: **18/18 tests passing**

**Key Agentic Behaviors Verified:**

1. **Unknown Spec Works Without Provider Code:**
   ```
   Provider inferred: acme_widgets  (from spec title "Acme Widget API")
   Task slug: create_widget
   Template source: inferred  (no hardcoded templates used)
   ```

2. **HTTP Method → Workflow Pattern Inference:**
   - POST → validate_input → call_create → transform_response
   - GET w/params → validate_id → call_get → handle_not_found
   - GET list → build_query → call_list → paginate_response
   - DELETE → validate_id → call_delete → confirm_deleted

3. **External API Specs Work (No Pre-configuration):**
   - Petstore: ✅ Provider inferred, workflows generated
   - JSONPlaceholder: ✅ Provider inferred, workflows generated
   - Acme Widgets: ✅ Provider inferred, workflows generated

4. **Cross-Spec Pattern Consistency:**
   - CREATE pattern identical across Petstore, JSONPlaceholder, Acme
   - LIST pattern identical across all three
   - No provider-specific code paths

### LLM-Driven Task Understanding: ✅ CONFIRMED WORKING

**With Real LLM (Anthropic Claude):**
```
Task: "Create a new blog post"
Spec: jsonplaceholder_openapi.yaml
→ Task slug: create_blog_post
→ Workflow: start → validate_input → call_create → transform_response → end
→ Correctly identified POST /posts endpoint
```

**Fallback Heuristics (Mock Mode):**
- Correctly extracts action words (create, update, delete, get)
- Matches against spec entities
- Infers workflow pattern from HTTP method

---

## The "Truly Agentic" Litmus Test

A system is truly agentic if:

| Test | Current | Target |
|------|---------|--------|
| Unknown spec URL works on first try | ✅ Works with smart inference | ✅ Infers pattern from HTTP method |
| Second run is better than first | ✅ KG learns templates | ✅ Same |
| Pattern from Provider A helps Provider B | ⚠️ Partial (via KG) | ✅ Pattern nodes transfer |
| No `--provider` flag needed | ✅ Infers from spec content | ✅ Same |
| Generated code runs without fixups | ⚠️ Import hack needed | ✅ Clean imports |

---

## Recommended M5 Task Additions

Based on this audit, add these to the M5 plan:

1. **WS1-T0 (NEW)**: Refactor provider inference to use spec content
   - File: `plan_run.py`, `detect_and_parse_spec.py`
   - Effort: S (2-3h)

2. **WS1-T5 (NEW)**: Dynamic policy inference from `securitySchemes`
   - File: `attach_policies_and_patterns.py`
   - Effort: M (4-5h)

3. **WS1-T6 (NEW)**: Add "unknown spec" tests to prove dynamism
   - File: `tests/test_dynamic_spec.py`
   - Effort: S (2-3h)

---

*Audit prepared November 28, 2025*
