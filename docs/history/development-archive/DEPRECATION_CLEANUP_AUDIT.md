# Deprecation & Legacy Code Cleanup Audit

**Generated**: December 2024  
**Based on**: Architecture audit findings (P0, P1, P2)  
**Purpose**: Identify deprecated/legacy code candidates for deletion or cleanup  
**Status**: ✅ **Items 1-9 COMPLETED** (December 2024)

---

## Executive Summary

This audit identifies deprecated code patterns in the codebase based on architecture documentation analysis. Items are categorized by cleanup priority and removal complexity.

| Priority | Count | Status |
|----------|-------|--------|
| 🔴 P0 | 3 | ✅ COMPLETED - All dead files deleted |
| 🟠 P1 | 5 | ✅ COMPLETED - All deprecated functions removed |
| 🟡 P2 | 4 | ⏳ DEFERRED to v3.0 (archetype detection system) |
| ⚪ P3 | 3 | ⏳ Documentation-only, no action needed |

---

## ✅ COMPLETED: P0 Immediate Deletion

### 1. `persist_results_backup.py` - ✅ DELETED

**Location**: `src/integration_coworker/graph/nodes/persist_results_backup.py`

**Status**: Deleted December 2024

---

### 2. `persist_results.py.broken` - ✅ DELETED

**Location**: `src/integration_coworker/graph/nodes/persist_results.py.broken`

**Status**: Deleted December 2024

---

### 3. `models.yaml` - ✅ DELETED

**Location**: `src/integration_coworker/config/models.yaml`

**Status**: Deleted December 2024 (after migrating all callers)

---

## ✅ COMPLETED: P1 Deprecated Functions Removed

### 4. `call_llm()` Function - ✅ REMOVED

**Location**: `src/integration_coworker/llm/client.py`

**Migration**: 
- `repo/llm_inference.py` migrated to `call_llm_for_node("repo_config_inference", ...)`
- Created `config/archetypes/repo_config_inference.archetype.yaml`

**Status**: Function removed December 2024

---

### 5. `call_llm_json()` Function - ✅ REMOVED

**Location**: `src/integration_coworker/llm/client.py`

**Status**: Function removed December 2024

---

### 6. `get_llm_config()` Function - ✅ REMOVED

**Location**: `src/integration_coworker/config/__init__.py`

**Migration**: `get_llm_client()` updated to use empty config with defaults

**Status**: Function removed December 2024

---

### 7. `_refine_codes_batch()` Function - ✅ REMOVED

**Location**: `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

**Status**: Function removed December 2024

---

### 8. `MockFile` Class - ✅ REMOVED

**Location**: `src/integration_coworker/repo/models.py`

**Migration**: 
- `repo/context.py` updated to use `SourceFile`
- Removed from `repo/__init__.py` exports

**Status**: Class removed December 2024

---

### 9. `call_llm_async()` Function - ✅ REMOVED

**Location**: `src/integration_coworker/llm/async_client.py`

**Migration**: `run_concurrent_llm_calls()` updated to use archetype-based approach

**Status**: Function removed December 2024

---

## 🟡 P2: Deferred to v3.0 (Legacy Fallback Code)

These are deprecated fallback paths that provide backward compatibility.

### 10. Framework Archetype Detection System

**Location**: `src/integration_coworker/repo/detection.py`

**Components**:
- `FrameworkArchetypeConfig` class (lines 55-99) - deprecated per ADR-0002
- `KNOWN_ARCHETYPES` dict (lines 101-650+) - ~550 lines of framework configs
- `detect_archetype()` function (lines 654+)

**Status**: 
- Marked "deprecated:: 2.1", "Will be removed in v3.0"
- Emits `DeprecationWarning` when called
- ADR-0002 recommends config-first approach

**Replacement**: `.integration-coworker.yaml` config files per repo

**Active Usages**:
| File | Usage |
|------|-------|
| `repo/profiles.py` | Fallback when config file not found |
| `repo/llm_inference.py` | Priority 3 fallback for profile detection |
| `graph/nodes/attach_repo_context.py` | Priority 3 fallback |

**Action**:
1. Ensure LLM inference (Priority 2) works reliably
2. Gate archetype fallback behind stricter conditions
3. Remove in v3.0

---

### 10. Archetype Profile Functions

**Location**: `src/integration_coworker/repo/profiles.py`

**Deprecated Functions**:
- `profile_for_archetype()` - creates profiles from deprecated archetypes
- `_mark_deprecated()` - utility to mark profiles as deprecated

**Evidence**: 
- 10+ occurrences of `profile.profile_source = "archetype_deprecated"`
- Docstring says "DEPRECATED (ADR-0002)"

**Action**: Remove alongside `detection.py` cleanup

---

### 11. `call_llm_async()` Legacy Function

**Location**: `src/integration_coworker/llm/async_client.py:860-900`

**Status**: Docstring says "DEPRECATED: Use call_llm_async_for_node()"

**Action**: Same migration path as sync `call_llm()`

---

### 12. SQLite Production Warning

**Location**: `src/integration_coworker/persistence/db.py:179-201`

**Status**: `USE_SQLITE` emits `DeprecationWarning` for production

**This is correct behavior** - SQLite is deprecated for production, Postgres required.

**Action**: Keep the warning, possibly escalate to error in v3.0

---

## ⚪ P3: Documentation-Only Cleanup

These are doc references to removed code.

### 13. `_LEGACY_WORKFLOW_TEMPLATES` References

**Status**: ✅ **Already removed from source** per V2_CRITICAL_REFLECTION_AUDIT.md

**Remaining References**: Only in `docs/` historical files (correct - for context)

**Action**: No code change needed

---

### 14. `USE_LEGACY_TEMPLATES` / `USE_IN_MEMORY_KG_FALLBACK` Flags

**Status**: ✅ **Removed from source** per V2_IMPLEMENTATION_AUDIT.md

**Remaining References**: 
- `tests/test_semantic_retrieval_probing.py:45` - sets env var
- `tests/test_graphrag_integration.py:33-34` - deletes env var
- Historical docs

**Action**: Clean up test env var handling if flags are fully removed

---

## Cleanup Execution Plan

### Phase 1: Immediate (P0) - 30 minutes
```bash
# Delete dead files
rm src/integration_coworker/graph/nodes/persist_results_backup.py
rm src/integration_coworker/graph/nodes/persist_results.py.broken
```

### Phase 2: LLM Function Migration (P1) - 2-4 hours

1. **Create archetype for `repo_config_inference`**:
   - Add `config/archetypes/repo_config_inference.archetype.yaml`
   - Update `repo/llm_inference.py` to use `call_llm_for_node()`

2. **Add deprecation escalation**:
   - Change `call_llm()` warning to error
   - Same for `call_llm_json()`, `get_llm_config()`

3. **Delete deprecated functions** in next major version

### Phase 3: MockFile Migration (P1) - 1-2 hours

1. Update `repo/context.py` to create `SourceFile` directly
2. Remove `MockFile` from `repo/__init__.py` exports
3. Delete `MockFile` class

### Phase 4: Archetype System Deprecation (P2) - Deferred to v3.0

1. Verify LLM inference works reliably for all test cases
2. Remove `KNOWN_ARCHETYPES` and related code (~700 lines)
3. Update docs

### Phase 5: Final Cleanup (P3)

1. Remove `models.yaml`
2. Clean up test env var handling
3. Update CHANGELOG

---

## Impact Analysis

| Item | Lines Removed | Risk | Dependencies |
|------|--------------|------|--------------|
| `persist_results_backup.py` | ~200 | Low | None |
| `persist_results.py.broken` | ~220 | Low | None |
| `call_llm()` family | ~150 | Medium | Need migration |
| `MockFile` | ~50 | Low | Update `context.py` |
| `models.yaml` | ~50 | Low | After function removal |
| `detection.py` archetypes | ~700 | High | Wait for v3.0 |

**Total Potential Cleanup**: ~1,370 lines of deprecated code

---

## Verification Commands

```bash
# Verify no imports of deleted files
grep -r "persist_results_backup" src/ tests/
grep -r "models\.yaml" src/ --include="*.py"

# Verify deprecated function usage
grep -r "call_llm(" src/ --include="*.py" | grep -v "call_llm_for_node\|call_llm_async_for_node"
grep -r "get_llm_config(" src/ --include="*.py"
grep -r "MockFile" src/ --include="*.py"

# Run tests after cleanup
pytest tests/ -v --tb=short
```

---

## Appendix: Files Inventory

### Candidates for Deletion (P0)
- `src/integration_coworker/graph/nodes/persist_results_backup.py`
- `src/integration_coworker/graph/nodes/persist_results.py.broken`

### Candidates for Deprecation Removal (P1/P2)
- `src/integration_coworker/config/models.yaml` (after migration)
- `src/integration_coworker/llm/client.py` (functions: `call_llm`, `call_llm_json`)
- `src/integration_coworker/llm/async_client.py` (function: `call_llm_async`)
- `src/integration_coworker/config/__init__.py` (function: `get_llm_config`)
- `src/integration_coworker/repo/models.py` (class: `MockFile`)
- `src/integration_coworker/repo/detection.py` (class: `FrameworkArchetypeConfig`, dict: `KNOWN_ARCHETYPES`)
- `src/integration_coworker/repo/profiles.py` (deprecated archetype functions)
- `src/integration_coworker/graph/nodes/generate_code_and_tests.py` (function: `_refine_codes_batch`)
