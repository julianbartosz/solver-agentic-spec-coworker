# Session Changes Audit: M5 Implementation Record

**Created:** November 29, 2025  
**Purpose:** Record all major edits made during M5 implementation sessions to prevent regression and enable correctness verification.

---

## Executive Summary

This session focused on completing the **M5 milestone** with emphasis on:
1. **Dynamic Spec Handling** — Remove hardcoded provider dependencies
2. **RepoProfile v2** — Two-layer detection and inference system
3. **Agentic Behavior Verification** — Sanity check that system is truly dynamic

### Key Metrics

| Metric | Before | After |
|--------|--------|-------|
| Tests | 158 | 246+ |
| Known Archetypes | 3 | 6 |
| Hardcoded Provider List | In `plan_run.py` | Removed |
| Legacy Templates | Always used | Gated (default OFF) |
| Golden Repo Fixtures | 0 | 8 |

---

## Change Category 1: Dynamic Provider Inference

### Problem
`plan_run.py` had a hardcoded list of known providers:
```python
if "stripe" in primary_ref.lower():
    state.provider_code = "stripe"
elif "mock_payments" in primary_ref.lower():
    state.provider_code = "mock_payments"
# ... etc
```

### Solution Approach
Replaced with dynamic inference from spec content:
1. **Strategy 1**: `spec.servers[0].url` domain extraction
2. **Strategy 2**: `spec.info.title` slug conversion
3. **Strategy 3**: URL/filepath extraction as fallback

### Files Modified

| File | Change Type | Description |
|------|-------------|-------------|
| `src/integration_coworker/graph/nodes/plan_run.py` | REFACTOR | Added `infer_provider_code()`, `_infer_provider_from_url()`, `_infer_provider_from_title()`, `_infer_provider_from_filepath()` |
| `tests/test_dynamic_spec.py` | NEW | 10 tests for dynamic provider inference |
| `tests/fixtures/acme_widgets_openapi.yaml` | NEW | Test fixture for "unknown" provider |

### Key Code Added

```python
# plan_run.py
def infer_provider_code(spec_ref: str, parsed_spec: Optional[dict] = None) -> str:
    """
    Infer provider_code from spec content and/or spec reference.
    M5 Architecture: Dynamic inference, no hardcoded provider list.
    
    Priority order:
    1. spec.servers[0].url domain (most reliable for real APIs)
    2. spec.info.title (good for descriptive specs)
    3. Filepath/URL of spec itself (fallback)
    """
```

### Verification Commands
```bash
# Run dynamic spec tests
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/test_dynamic_spec.py -v

# Verify no hardcoded provider list remains
grep -r "elif.*mock_payments\|elif.*stripe\|elif.*github" src/
# Should return nothing
```

---

## Change Category 2: Legacy Templates Gating

### Problem
`_LEGACY_WORKFLOW_TEMPLATES` dict was always consulted, creating false impression that providers must be "added" manually.

### Solution Approach
Gate behind `USE_LEGACY_TEMPLATES` environment variable (default: OFF):
1. Add `_check_legacy_templates_enabled()` function
2. Only consult legacy dict when explicitly enabled
3. Use smarter `_infer_workflow_from_endpoint()` as default fallback

### Files Modified

| File | Change Type | Description |
|------|-------------|-------------|
| `src/integration_coworker/graph/nodes/align_task_with_kg.py` | REFACTOR | Added env var gate, `_infer_workflow_from_endpoint()`, `_find_matching_endpoint()` |
| `tests/test_align_task_with_kg.py` | UPDATED | Now explicitly enables `USE_LEGACY_TEMPLATES=1` for backwards compat tests |
| `tests/test_dynamic_spec.py` | NEW | Tests for gating behavior |

### Key Code Added

```python
# align_task_with_kg.py
def _check_legacy_templates_enabled() -> bool:
    """
    Check if legacy hardcoded templates are enabled.
    M5 Architecture: Legacy templates are DEPRECATED.
    Default is OFF (0). Set USE_LEGACY_TEMPLATES=1 to enable.
    """
    return os.environ.get("USE_LEGACY_TEMPLATES", "0") == "1"

def _infer_workflow_from_endpoint(endpoint: Optional[Endpoint], task_description: str) -> List[dict]:
    """
    Infer workflow steps from endpoint structure (M5: dynamic, no hardcoding).
    
    Pattern mapping:
    - POST with request body → validate_input → create_resource → return_created
    - GET with path params → validate_id → fetch_resource → return_or_404
    - GET without params → build_query → list_resources → paginate_response
    - PUT/PATCH → validate_input → fetch_existing → update_resource
    - DELETE → validate_id → delete_resource → confirm_deleted
    """
```

### Fallback Hierarchy (New)
1. **KG templates** (from GraphRAG) — primary path
2. **Legacy in-memory templates** (only if `USE_LEGACY_TEMPLATES=1`)
3. **In-memory KG fallback** (only if `USE_IN_MEMORY_KG_FALLBACK=1`)
4. **Smart generic fallback** (HTTP method inference) — M5 default

### Verification Commands
```bash
# Verify legacy templates OFF by default
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/test_dynamic_spec.py::TestLegacyTemplatesGating -v

# Verify existing tests still pass with legacy enabled
USE_LEGACY_TEMPLATES=1 USE_SQLITE=true USE_MOCK_LLM=true pytest tests/test_align_task_with_kg.py -v
```

---

## Change Category 3: RepoProfile v2 Two-Layer Detection

### Problem
Original detection was single-pass with no confidence tracking, making it hard to know when layout inference was reliable.

### Solution Approach
Two-layer architecture:
1. **Layer 1 (Detection)**: `detect_repo_profile()` → `DetectedProfile` with confidence score
2. **Layer 2 (Inference)**: `build_effective_repo_profile()` → `RepoProfile` with layout

### Files Modified/Created

| File | Change Type | Description |
|------|-------------|-------------|
| `src/integration_coworker/repo/detection.py` | REFACTOR | Major rewrite with two-layer system |
| `src/integration_coworker/repo/models.py` | UPDATED | Added `DetectedProfile` dataclass |
| `tests/repo/test_detection_profiles_e2e.py` | NEW | 29 E2E tests for detection |
| `tests/repo/test_detection_edge_cases.py` | NEW | 30 edge case tests |
| `tests/fixtures/repos/*` | NEW | 8 golden repo fixtures |
| `docs/APPENDIX_REPO_PROFILES_V2.md` | NEW | Architecture documentation |

### Known Archetypes (6 total)

| Archetype | Language | Detection Signals |
|-----------|----------|-------------------|
| `fastapi` | Python | `fastapi` dep, `main.py` |
| `django` | Python | `manage.py`, `django` dep |
| `flask` | Python | `flask` dep, `app.py` |
| `nextjs` | TypeScript | `next.config.js`, `next` dep |
| `nestjs` | TypeScript | `@nestjs/core` dep |
| `express` | TypeScript | `express` dep |

### Confidence Thresholds

```python
HIGH_CONFIDENCE_THRESHOLD = 0.8    # Use archetype defaults
LOW_CONFIDENCE_THRESHOLD = 0.4     # Log warning, heuristic fallback
VERY_LOW_CONFIDENCE_THRESHOLD = 0.3  # Try LLM refinement if enabled
```

### Golden Repo Fixtures Created

```
tests/fixtures/repos/
├── django_service/      # Django with manage.py + settings dir
├── express_app/         # Express.js with app.js
├── fastapi_service/     # FastAPI with app/main.py
├── flask_service/       # Flask with app.py
├── generic_js/          # Plain JS project (no framework)
├── generic_python/      # Plain Python (no framework)
├── nestjs_app/          # NestJS with nest-cli.json
└── nextjs_app/          # Next.js with next.config.js
```

### Verification Commands
```bash
# Run all repo detection tests
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/repo/ tests/test_repo_profiles.py -v

# Run E2E pipeline tests with repo detection
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/test_end_to_end_repo_profiles.py -v
```

---

## Change Category 4: Import Path Fix

### Problem
Generated code used relative imports that broke with `PYTHONPATH=src`:
```python
# BROKEN - required package structure
from .clients.mock_payments import MockPaymentsClient
```

A workaround hack existed in tests:
```python
# test_m4_generated_code_execution.py (REMOVED)
content = content.replace("from .clients.mock_payments", "from integrations.clients.mock_payments")
```

### Solution Approach
Fixed code generation to use absolute imports and added proper exception imports.

### Files Modified

| File | Change Type | Description |
|------|-------------|-------------|
| `src/integration_coworker/graph/nodes/generate_code_and_tests.py` | FIX | Changed to absolute imports, added `IntegrationError` import |
| `tests/test_m4_generated_code_execution.py` | CLEANUP | Removed workaround hack |

### Key Code Change

```python
# generate_code_and_tests.py - Flow template now includes:
from integration_coworker.runtime.exceptions import IntegrationError

# And uses try/except:
try:
    response = client.{method_name}(...)
except Exception as e:
    raise IntegrationError(f"API call failed: {{str(e)}}") from e
```

### Verification Commands
```bash
# Verify generated code execution test passes without hack
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/test_m4_generated_code_execution.py -v
```

---

## Change Category 5: Build Report Updates

### Problem
Report didn't show:
- Profile detection confidence
- Detection evidence
- Low confidence warnings

### Solution Approach
Enhanced `build_report.py` to include RepoProfile v2 information.

### Files Modified

| File | Change Type | Description |
|------|-------------|-------------|
| `src/integration_coworker/graph/nodes/build_report.py` | ENHANCED | Added detection confidence, evidence, warnings |

### Report Sections Added

```markdown
## Repository Profile

| Attribute | Value |
|-----------|-------|
| Detected Archetype | fastapi |
| Detection Confidence | 95% (high) |
| Profile Source | archetype |

### Detection Evidence
- Found app/main.py
- Dependency: fastapi
- Dependency: uvicorn

### Layout Configuration
- Integrations root: app/integrations
- Tests root: tests/integrations
```

### Low Confidence Warning
```markdown
> ⚠️ **Low Detection Confidence**: Layout inference may be unreliable. 
> Consider providing an explicit `--repo-profile` flag.
```

---

## Change Category 6: Dependency Parsing Edge Cases

### Problem
Dependency extraction failed for:
- Extras: `fastapi[all]` → crashed
- Version specifiers: `flask>=2.0` → included version in name
- Comments: `# this is a comment` → treated as package

### Solution Approach
Added `_extract_pkg_name()` helper to handle all edge cases.

### Files Modified

| File | Change Type | Description |
|------|-------------|-------------|
| `src/integration_coworker/repo/detection.py` | FIX | Added `_extract_pkg_name()` helper, fixed `requirements.txt` parsing |
| `tests/repo/test_detection_edge_cases.py` | NEW | 30 tests for edge cases |

### Key Code Added

```python
def _extract_pkg_name(dep: str) -> Optional[str]:
    """
    Extract clean package name from dependency string.
    
    Handles:
    - Extras: fastapi[all] → fastapi
    - Version specifiers: flask>=2.0 → flask
    - Comments: # comment → None
    - Whitespace: "  package  " → package
    """
    dep = dep.strip()
    if not dep or dep.startswith("#") or dep.startswith("-"):
        return None
    
    # Remove extras: package[extra1,extra2] → package
    dep = re.sub(r'\[.*?\]', '', dep)
    
    # Remove version specifiers: >=, ==, ~=, etc.
    dep = re.split(r'[<>=!~;@]', dep)[0]
    
    return dep.strip().lower() if dep.strip() else None
```

### Verification Commands
```bash
# Run edge case tests
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/repo/test_detection_edge_cases.py -v
```

---

## Change Category 7: Sanity Check Documentation

### Problem
Need evidence document for boss showing system is truly agentic.

### Solution Approach
Created `SANITY_M5.md` with 8-section verification.

### Files Created

| File | Description |
|------|-------------|
| `docs/SANITY_M5.md` | Full sanity check results with evidence |

### Sections Verified

1. ✅ LLM Stack & LangSmith — All calls via LangChain
2. ✅ Agentic Behavior — KG-first, legacy templates gated
3. ✅ RepoProfile v2 Wiring — Two-layer detection in graph
4. ✅ Dynamic Inference — Provider from spec, auth from securitySchemes
5. ✅ KG Cross-Provider Patterns — STANDARD_PATTERNS defined
6. ✅ CLI & Health Tooling — All commands operational
7. ✅ Representative Tests — 71+ tests passing
8. ✅ Documentation — This document

---

## Change Category 8: Test Infrastructure

### New Test Files Created

| File | Tests | Purpose |
|------|-------|---------|
| `tests/test_dynamic_spec.py` | 10 | Dynamic provider inference |
| `tests/test_end_to_end_repo_profiles.py` | 12 | E2E with repo detection |
| `tests/repo/test_detection_profiles_e2e.py` | 29 | Detection E2E tests |
| `tests/repo/test_detection_edge_cases.py` | 30 | Dependency parsing edge cases |
| `tests/test_graph_traversal.py` | ~20 | KG graph traversal |
| `tests/test_policy_inference.py` | ~15 | Auth policy from spec |
| `tests/test_public_specs.py` | ~15 | Diverse spec handling |
| `tests/test_multi_provider_llm.py` | ~20 | LLM client tests |

### Test Fixtures Created

| Fixture | Purpose |
|---------|---------|
| `tests/fixtures/acme_widgets_openapi.yaml` | "Unknown" provider spec |
| `tests/fixtures/petstore_openapi.yaml` | Classic Swagger example |
| `tests/fixtures/jsonplaceholder_openapi.yaml` | Simple REST API |
| `tests/fixtures/repos/*` (8 dirs) | Golden repo fixtures |

### pyproject.toml Changes

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
norecursedirs = [
    "tests/fixtures/repos",  # Added - don't collect fixtures as tests
]
```

---

## Regression Prevention Checklist

### Before Making Changes, Verify:

```bash
# Full test suite passes
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v

# Legacy templates still work when enabled
USE_LEGACY_TEMPLATES=1 USE_IN_MEMORY_KG_FALLBACK=1 USE_SQLITE=true USE_MOCK_LLM=true \
  pytest tests/test_align_task_with_kg.py -v

# Dynamic spec tests pass
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/test_dynamic_spec.py -v

# Repo detection tests pass
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/repo/ -v

# E2E pipeline tests pass
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/test_end_to_end_integration.py tests/test_end_to_end_repo_profiles.py -v
```

### Key Invariants to Maintain

1. **Provider inference is dynamic**: No hardcoded provider list in `plan_run.py`
2. **Legacy templates are gated**: `USE_LEGACY_TEMPLATES=0` by default
3. **Confidence thresholds exist**: HIGH=0.8, LOW=0.4, VERY_LOW=0.3
4. **Import paths are absolute**: Generated code uses `integrations.clients.*`
5. **LangChain for LLM**: No direct OpenAI/Anthropic HTTP calls
6. **Two-layer detection**: `detect_repo_profile()` → `build_effective_repo_profile()`

---

## Architecture Decisions Made

| Decision | Rationale | Reference |
|----------|-----------|-----------|
| Gate legacy templates (default OFF) | Bootstrap artifact, not target architecture | `docs/AGENTIC_ARCHITECTURE_AUDIT.md` |
| HTTP method inference | Dynamic workflow without hardcoding | `align_task_with_kg.py:_infer_workflow_from_endpoint()` |
| Two-layer repo detection | Separate confidence from layout inference | `docs/APPENDIX_REPO_PROFILES_V2.md` |
| Absolute imports in codegen | Works with `PYTHONPATH=src` | `generate_code_and_tests.py` fix |
| Golden repo fixtures | Reliable test data for all archetypes | `tests/fixtures/repos/` |

---

## Files Summary

### New Files (25+)
- `tests/test_dynamic_spec.py`
- `tests/test_end_to_end_repo_profiles.py`
- `tests/test_graph_traversal.py`
- `tests/test_policy_inference.py`
- `tests/test_public_specs.py`
- `tests/test_multi_provider_llm.py`
- `tests/repo/test_detection_profiles_e2e.py`
- `tests/repo/test_detection_edge_cases.py`
- `tests/fixtures/acme_widgets_openapi.yaml`
- `tests/fixtures/petstore_openapi.yaml`
- `tests/fixtures/jsonplaceholder_openapi.yaml`
- `tests/fixtures/repos/*` (8 directories)
- `docs/SANITY_M5.md`
- `docs/APPENDIX_REPO_PROFILES_V2.md`
- `docs/AGENTIC_ARCHITECTURE_AUDIT.md`
- `docs/M5_IMPLEMENTATION_PLAN.md`
- `docs/M5_ROADMAP.md`

### Modified Files (15+)
- `src/integration_coworker/graph/nodes/plan_run.py`
- `src/integration_coworker/graph/nodes/align_task_with_kg.py`
- `src/integration_coworker/graph/nodes/generate_code_and_tests.py`
- `src/integration_coworker/graph/nodes/build_report.py`
- `src/integration_coworker/repo/detection.py`
- `tests/test_align_task_with_kg.py`
- `tests/test_graphrag_integration.py`
- `tests/test_m4_generated_code_execution.py`
- `pyproject.toml`

---

*Audit document prepared November 29, 2025*
