# Production Bug Report - December 10, 2024

## Summary

Comprehensive production testing was performed to verify the system's ability to:
- Generate syntactically valid Python code for real API integrations
- Persist results to PostgreSQL
- Handle multi-language code generation
- Use LLM inference for repo detection
- Apply strict/constrained codegen modes

## Test Environment

- **Database**: PostgreSQL with pgvector (Docker)
- **LLM Provider**: OpenAI GPT-4
- **Spec Used**: `httpbin_api.json` (HTTPBin API spec)
- **Modes Tested**: dry_run, strict_codegen, constrained_codegen, repo integration

## Verified Working Features ✅

### 1. Core Workflow Execution
- **17 workflow steps** complete successfully
- LangGraph orchestration functions correctly
- State management between nodes works

### 2. Syntax Validation (Bug #88 - FIXED)
- Tree-sitter based validation implemented for 7 languages
- AST fallback for Python when tree-sitter not installed
- Language alias normalization works (py→python, ts→typescript)
- All generated Python code passes syntax validation

### 3. Code Artifact Generation
- **Client code**: 240 lines generated
- **Flow code**: 80 lines generated  
- **Test code**: 166 lines generated
- All artifacts validated successfully

### 4. Content Security Policies
- Detects potential API keys in query strings
- Retries with policy violations (up to 2 retries)
- Falls back to skeleton when violations persist

### 5. Go Repo Detection
- Successfully detects Go repos with `go.mod`
- Heuristic detection working with proper `profile_source`

### 6. PostgreSQL Persistence
- Tables populated correctly:
  - spec_documents: populated
  - endpoints: populated
  - integration_tasks: populated
  - code_artifacts: populated
  - run_status: tracking runs

---

## Discovered Bugs 🐛

### Bug #89: LLM Returns Invalid Multi-Language Value [HIGH]

**Feature**: LLM Repo Config Inference

**Error**:
```
ValidationError: 1 validation error for IntegrationCoworkerConfig
profile.language
  Value error, Unsupported language 'python|typescript'. Supported languages: csharp, go, java, javascript, python, ruby, typescript
```

**Root Cause**: LLM is returning `'python|typescript'` for repos with both languages, but the schema expects a single language.

**Recommendation**: 
1. Update prompt to explicitly request single primary language
2. Add post-processing to extract first language from pipe-separated values
3. Consider adding `primary_language` and `secondary_languages` fields

**Affected File**: `src/integration_coworker/repo/llm_inference.py`

---

### Bug #90: LLM Config Inference Missing Required Fields [HIGH]

**Feature**: LLM Repo Config Inference

**Error**:
```
Config validation failed: 3 validation errors for IntegrationCoworkerConfig
profile.name - Field required
layout.integrations_root - Field required
layout.tests_root - Field required
```

**Root Cause**: LLM output doesn't include all required fields, only partial config.

**Recommendation**:
1. Update prompt to include all required fields with examples
2. Add default value population for missing required fields
3. Implement schema-aware prompt generation

**Affected File**: `src/integration_coworker/repo/llm_inference.py`

---

### Bug #91: Database Connection Pool Cleanup Warnings [MEDIUM]

**Feature**: Database Persistence

**Warning**:
```
rolling back returned connection: <psycopg.Connection [INTRANS]>
ConnectionWrapper was garbage collected without being closed.
Use 'with db.get_connection() as conn:' pattern for proper cleanup.
```

**Root Cause**: Some code paths acquire connections but don't properly close them, leading to rollbacks on garbage collection.

**Recommendation**:
1. Audit all `get_connection()` calls for proper context manager usage
2. Consider implementing connection pool timeout/cleanup
3. Add connection leak detection in tests

**Affected File**: `src/integration_coworker/persistence/db.py`

---

### Bug #92: EndpointBinding Missing endpoint_id [LOW]

**Feature**: Integration Flow Planning

**Warning**:
```
Warning: EndpointBinding for node 'call_api' has endpoint_id=None
```

**Root Cause**: When creating endpoint bindings, the endpoint_id isn't always set before flow construction.

**Recommendation**:
1. Ensure endpoint persistence completes before binding creation
2. Add validation in flow construction to require endpoint_id

**Affected File**: `src/integration_coworker/graph/nodes/plan_integration_flow.py`

---

### Bug #93: TypeScript Skeleton Fallback Triggered [MEDIUM]

**Feature**: Multi-Language Code Generation

**Warning**:
```
Falling back to typescript skeleton for client 'TsCodegenTestClient' because: 
LLM output missing expected function 'get_anything'
```

**Root Cause**: LLM doesn't always generate all expected functions, triggering skeleton fallback.

**Recommendation**:
1. Improve prompt with explicit function requirements
2. Add retry with more specific instructions
3. Consider partial acceptance of LLM output

**Affected File**: `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

---

## Test Infrastructure Bugs (Fixed During Session) ✅

### Fixed: SourceRef API Mismatch
- Changed `SourceRef(ref=...)` to `SourceRef.from_ref(...)`
- The dataclass requires `id`, `uri`, `ref_type` fields, not `ref`

### Fixed: WorkflowState Required Fields
- Both `source_refs` and `spec_refs` are required
- Tests updated to provide both fields

### Fixed: Async/Sync Runtime Mismatch
- `run_workflow()` uses `asyncio.run()` internally
- Tests using `async def` must call `_run_workflow_async()` directly

### Fixed: PostgreSQL Column Name
- Column is `finished_at`, not `completed_at`
- Updated test queries

### Fixed: Import Path for LLM Inference
- Function is `infer_repo_config()`, not `infer_repo_config_with_llm()`
- Function is synchronous, not async

### Fixed: Profile Source Access
- Use `get_repo_profile()` not `detect_repo_profile()` for `profile_source`
- `DetectedProfile` doesn't have `profile_source`, `RepoProfile` does

---

## Performance Observations

### Timing
- Full workflow (17 steps): ~30-60 seconds with LLM calls
- Spec parsing: Fast (<1s for httpbin_api.json)
- LLM calls: Main bottleneck (retries add latency)

### Memory
- Connection pool warnings suggest room for optimization
- Large specs (Stripe 7MB) may need streaming

---

## Recommendations

### Immediate Priority (P0)
1. Fix Bug #89 - LLM language parsing
2. Fix Bug #90 - LLM config required fields

### Short-Term (P1)
3. Fix Bug #91 - Connection cleanup
4. Improve TypeScript codegen prompts

### Monitor (P2)
5. EndpointBinding warning (cosmetic)

---

## Test Command

```bash
cd /Users/julianbartosz/git/repos/solver-agentic-spec-coworker
source .venv/bin/activate
python scripts/test_production_dec10.py
```

## Quick Verification

```python
# Quick test that workflow completes
import asyncio
from integration_coworker.graph.runtime import _run_workflow_async
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SourceRef
from integration_coworker.api.types import IntegrationOptions

async def test():
    state = WorkflowState(
        source_refs=[SourceRef.from_ref('specs/httpbin_api.json', 'test')],
        spec_refs=['specs/httpbin_api.json'],
        task_description='Make a GET request',
        provider_code='production_test',
        options=IntegrationOptions(dry_run=True),
    )
    result = await _run_workflow_async(state)
    print(f'Steps: {len(result.completed_steps)}, Artifacts: {len(result.code_artifacts)}')

asyncio.run(test())
```

---

## Conclusion

The core integration workflow is **production-ready** for Python code generation against OpenAPI specs. The main issues are:

1. **LLM inference for complex repos** needs prompt improvements
2. **Connection pool cleanup** needs attention
3. **TypeScript generation** has higher skeleton fallback rate

All generated Python code passes syntax validation, and the workflow completes successfully with proper artifact generation.
