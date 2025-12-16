# M5 Roadmap: Dynamic Spec Architecture

**Document Version**: 5.0  
**Last Updated**: November 29, 2025  
**Status**: ✅ M5 COMPLETE  
**Detailed Plan**: `docs/M5_IMPLEMENTATION_PLAN.md`  
**Audit Reference**: `docs/AGENTIC_ARCHITECTURE_AUDIT.md`

---

## 🎉 Status Update: M5 COMPLETE

**Tests**: 158 → 246+ (88+ new tests added!)

### ✅ Completed P0 Items (Week 1):
- **WS1-T1**: Legacy templates gated behind `USE_LEGACY_TEMPLATES` (default: OFF)
- **WS1-T2**: Smarter generic fallback with `_infer_workflow_from_endpoint()`
- **WS1-T3**: Provider inference from spec content (not hardcoded list)
- **WS3-T1**: Fixed relative import problem (removed test hack)
- **WS1-T6**: Created `test_dynamic_spec.py` with 10 tests

### ✅ Completed P1 Items (Week 2):
- **WS1-T5**: Dynamic policy inference from `securitySchemes` (bearer, API key, OAuth2, etc.)
- **WS1-T4**: Diverse public spec tests (Petstore, JSONPlaceholder fixtures)
- **WS2-T3**: Template source shown in run report (KG/pattern/inferred)
- **WS3-T2**: Syntax validation for generated code (AST parsing)
- **WS3-T3**: Added 3 new repo archetypes (Flask, Express, NestJS)
- **WS2-T1**: Cross-provider pattern matching (pattern nodes with BFS/DFS)
- **WS2-T2**: Graph traversal task matching (BFS/DFS, not embeddings)
- **WS2-T4**: `kg-query` CLI command (graph-based queries)

### ✅ Completed P2 Items (Week 3-4):
- **WS4-T1**: `--verbose` CLI flag for debug logging
- **WS4-T2**: `health` command with system checks
- **WS4-T3**: LangSmith observability documentation
- **WS4-T4**: Troubleshooting section in DEMO.md

### Key Changes Made:
| File | Change |
|------|--------|
| `align_task_with_kg.py` | Added `_check_legacy_templates_enabled()`, `_infer_workflow_from_endpoint()` |
| `plan_run.py` | Added `infer_provider_from_spec_content()`, removed hardcoded provider list |
| `generate_code_and_tests.py` | Fixed IntegrationError import path |
| `attach_policies_and_patterns.py` | Dynamic auth inference from securitySchemes |
| `validate_integration_design.py` | Added `_validate_python_syntax()` using AST |
| `build_report.py` | Added "Template Selection" section with source |
| `profiles.py` | Added Flask, Express, NestJS profiles (7 total archetypes) |
| `kg/__init__.py` | Added BFS/DFS traversal, pattern matching |
| `cli.py` | Added `kg-query`, `health` commands, `--verbose` flag |
| `docs/demo.md` | LangSmith docs, troubleshooting section |

### New Test Files:
| File | Tests |
|------|-------|
| `test_dynamic_spec.py` | 10 tests |
| `test_policy_inference.py` | 13 tests |
| `test_public_specs.py` | 18 tests |
| `test_repo_profiles.py` | 32 tests (updated) |
| `test_cross_provider_patterns.py` | 21 tests |
| `test_graph_traversal.py` | 15 tests |

---

## 1. Strategic Pivot: From "Add Providers" to "Truly Agentic"

### The Old Approach (Wrong ❌)
> "Add Stripe, HubSpot, GitHub as providers with hardcoded templates"

### The New Approach (Correct ✅)
> "Any valid OpenAPI spec works on first run; system learns patterns and generalizes"

### Key Insight
The `_LEGACY_WORKFLOW_TEMPLATES` dict in `align_task_with_kg.py` is a **bootstrap artifact**, not the target architecture. It creates a false impression that providers must be "added" manually.

**The Real Flow:**
```
ANY OpenAPI Spec → Parse → Infer Provider → Query KG → Generic Fallback → Generate Code
                                              ↓
                                    (learns for next run)
```

---

## 2. Audit Findings Summary

We identified **7 anti-patterns** preventing truly dynamic behavior:

| # | Anti-Pattern | Severity | Status |
|---|--------------|----------|--------|
| 1 | `_LEGACY_WORKFLOW_TEMPLATES` hardcoded dict | 🔴 Critical | ✅ FIXED (gated) |
| 2 | Hardcoded provider detection in `plan_run.py` | 🟡 Medium | ✅ FIXED |
| 3 | Static policy attachment (always bearer auth) | 🟡 Medium | ✅ FIXED |
| 4 | Import path hack in test | 🔴 Critical | ✅ FIXED |
| 5 | Tests require explicit `provider_code` | 🟡 Medium | ✅ FIXED |
| 6 | Generic repo profile named "subatomic mock" | 🟢 Low | ✅ 7 archetypes now |
| 7 | No cross-provider pattern learning | 🟡 Medium | 🔄 Next |

See `docs/AGENTIC_ARCHITECTURE_AUDIT.md` for full details.

---

## 3. Goals for M5

1. ✅ **Any spec works on first run** — No hardcoded provider dependencies
2. ✅ **Deprecate legacy templates** — Gate behind env var (default: OFF)
3. 🔄 **Cross-provider learning** — Patterns from Stripe help with HubSpot
4. ✅ **Spec-aware policies** — Auth inferred from `securitySchemes`
5. ✅ **Clean generated code** — No import hacks needed
6. ✅ **Observability** — Template source visible in reports

---

## 4. Workstreams Overview

### Workstream 1: Dynamic Spec Handling (P0)

| Task | Description | Effort |
|------|-------------|--------|
| WS1-T1 | Deprecate `_LEGACY_WORKFLOW_TEMPLATES` (env var gate) | M |
| WS1-T2 | Smarter generic fallback (HTTP method → workflow pattern) | M |
| WS1-T3 | Provider inference from spec content (not hardcoded list) | S |
| WS1-T4 | Test with diverse public specs | M |
| WS1-T5 | Dynamic policy inference from `securitySchemes` | M |
| WS1-T6 | Add "unknown spec" tests (no `provider_code` passed) | S |

### Workstream 2: KG/GraphRAG Maturity (P1)

| Task | Description | Effort |
|------|-------------|--------|
| WS2-T1 | Cross-provider pattern matching (pattern nodes) | M |
| WS2-T2 | Graph traversal task matching (BFS/DFS, not embeddings) | M |
| WS2-T3 | KG metrics in run report (template source) | S |
| WS2-T4 | `kg-query` CLI command (works without --provider) | S |

### Workstream 3: Codegen Polish (P0-P1)

| Task | Description | Effort |
|------|-------------|--------|
| WS3-T1 | Fix relative import problem (remove test hack) | M |
| WS3-T2 | Post-generation syntax validation | S |
| WS3-T3 | Add more repo archetypes (Flask, Express, NestJS) | M |

### Workstream 4: Observability & Ergonomics (P2)

| Task | Description | Effort |
|------|-------------|--------|
| WS4-T1 | `--verbose` CLI flag | M |
| WS4-T2 | Health check command | S |
| WS4-T3 | LangSmith documentation | S |
| WS4-T4 | Troubleshooting section in DEMO.md | S |

---

## 5. Priority Matrix

| ID | Item | Priority | Status | Impact |
|----|------|----------|--------|--------|
| WS1-T1 | Deprecate legacy templates | P0 | ✅ DONE | Critical |
| WS1-T2 | Smarter generic fallback | P0 | ✅ DONE | High |
| WS1-T3 | Provider inference from spec | P0 | ✅ DONE | High |
| WS3-T1 | Fix import problem | P0 | ✅ DONE | High |
| WS1-T6 | Unknown spec tests | P1 | ✅ DONE | Medium |
| WS1-T5 | Dynamic policy inference | P1 | ✅ DONE | Medium |
| WS1-T4 | Diverse spec tests | P1 | ✅ DONE | Medium |
| WS2-T3 | KG metrics in report | P1 | ✅ DONE | Medium |
| WS3-T2 | Syntax validation | P1 | ✅ DONE | Medium |
| WS3-T3 | More archetypes | P1 | ✅ DONE | Low |
| WS2-T1 | Cross-provider patterns | P1 | ✅ DONE | High |
| WS2-T2 | Graph traversal matching | P1 | ✅ DONE | Medium |
| WS2-T4 | kg-query CLI | P1 | ✅ DONE | Medium |
| WS4-T1 | Verbose flag | P2 | ✅ DONE | Low |
| WS4-T2 | Health check | P2 | ✅ DONE | Low |
| WS4-T3 | LangSmith docs | P2 | ✅ DONE | Low |
| WS4-T4 | Troubleshooting | P2 | ✅ DONE | Low |

---

## 6. Timeline

### Week 1: Dynamic Foundation ✅ COMPLETE
- ✅ WS1-T1: Deprecate legacy templates (env var gate)
- ✅ WS1-T2: Smarter generic fallback
- ✅ WS3-T1: Fix import problem
- ✅ WS1-T3: Provider inference from spec content
- ✅ WS1-T6: Unknown spec tests

**Checkpoint**: ✅ Unknown specs generate valid code without hardcoded templates

### Week 2: Cross-Provider Learning + Policy Inference
- WS2-T1: Pattern-level KG nodes
- WS1-T5: Dynamic policy inference
- WS1-T4: Public spec tests
- WS2-T3: KG metrics in report

**Checkpoint**: Cross-provider pattern matching works, auth inferred from spec

### Week 3: Polish & Archetypes
- WS3-T3: Flask, Express, NestJS profiles
- WS3-T2: Syntax validation
- WS2-T2: Semantic task matching
- WS2-T4: kg-query CLI
- WS4-T1: Verbose flag

**Checkpoint**: 6 repo archetypes, kg-query works

### Week 4: Documentation & Release
- WS4-T2: Health check
- WS4-T3: LangSmith docs
- WS4-T4: Troubleshooting
- Final regression testing
- Tag `m5-complete`

---

## 7. Definition of Done for M5

| Criterion | Target | Measurement |
|-----------|--------|-------------|
| Arbitrary specs work | Without hardcoded templates | `test_dynamic_spec.py` passes |
| Legacy templates | Deprecated (default OFF) | `USE_LEGACY_TEMPLATES=0` |
| Hardcoded provider list | Removed | `plan_run.py` uses spec inference |
| Cross-provider learning | Patterns transfer | `test_cross_provider_learning.py` |
| Dynamic policies | From `securitySchemes` | `test_policy_inference.py` |
| Unknown spec tests | No `provider_code` needed | 5+ tests in `test_dynamic_spec.py` |
| Import hack | Removed | Test is clean |
| Repo archetypes | 6 total | Detection tests pass |
| Tests | 180+ | `pytest tests -v` |
| Documentation | Complete | LangSmith, troubleshooting |
| Tagged release | `m5-complete` | Git tag pushed |

---

## 8. The "Truly Agentic" Litmus Test

After M5, the system passes these tests:

| Test | Before M5 | After M5 |
|------|-----------|----------|
| Unknown spec works on first try | ⚠️ Generic 4-step | ✅ HTTP method → pattern |
| Second run is better than first | ✅ KG learns | ✅ Same |
| Pattern from Stripe helps HubSpot | ❌ No transfer | ✅ Pattern nodes |
| No `--provider` flag needed | ⚠️ Filename fallback | ✅ From spec content |
| Generated code runs without fixes | ❌ Import hack | ✅ Clean imports |
| Policies match API requirements | ❌ Always bearer | ✅ From `securitySchemes` |

---

## 9. What We're NOT Doing in M5

| Not Doing | Why |
|-----------|-----|
| Adding more hardcoded providers | Wrong direction |
| Manual `_LEGACY_WORKFLOW_TEMPLATES` entries | Deprecated |
| Provider-specific code paths | Should be dynamic |
| Extensive real LLM testing | Mock mode is sufficient |
| Production deployment features | Out of scope |

---

*Document revised November 28, 2025 — Dynamic Spec Architecture*
