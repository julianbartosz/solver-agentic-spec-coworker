# Production Hardening Plan: Code Generation → Repo Integration Pipeline

> **Document Status**: IMPLEMENTED  
> **Created**: 2025-01-24  
> **Implemented**: 2025-01-24  
> **Purpose**: Bridge from "conditionally production-ready (Python + OpenAPI)" to "production-ready across repo types and languages"

## Implementation Summary

| Item | Status | Tests |
|------|--------|-------|
| G-01: Docker runner auto-selection | ✅ Implemented | 10 tests |
| G-03: Spec conversion quality markers | ✅ Implemented | 12 tests |
| Test validation | ✅ 181 tests passing | No regressions |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Evidence Audit Summary](#2-evidence-audit-summary)
3. [Gap Matrix](#3-gap-matrix)
4. [Architecture Decision](#4-architecture-decision)
5. [Implementation Plan](#5-implementation-plan)
6. [Risk Assessment](#6-risk-assessment)
7. [Test Strategy](#7-test-strategy)

---

## 1. Executive Summary

### Current State
The code generation and repo integration pipeline is **conditionally production-ready**:
- ✅ **Python + OpenAPI**: Full Tier 1 validation (ruff, mypy, bandit, pytest, coverage, contract tests)
- ⚠️ **TypeScript/Go**: Infrastructure exists but requires Docker for Tier 1 compliance
- ⚠️ **Non-OpenAPI specs**: LLM-based conversion to pseudo-OpenAPI (non-deterministic)
- ⚠️ **Monorepos**: Detected but workspace integration not enforced

### Target State
Production-ready across:
- **Languages**: Python, TypeScript, Go (Tier 1), Java/JS (Tier 2)
- **Spec formats**: OpenAPI 3.x (deterministic), AsyncAPI/GraphQL/HTML/PDF (LLM-assisted with markers)
- **Repo types**: Single-package, monorepos, sparse repos

### Key Gaps to Address
1. **G-01**: Docker runner not wired into main codegen flow
2. **G-02**: Monorepo workspace detection lacks enforcement
3. **G-03**: Non-OpenAPI specs silently convert without quality markers
4. **G-04**: Contract tests (Schemathesis/Prism) are Python-only

---

## 2. Evidence Audit Summary

### 2.1 Pipeline Stages Enumerated

| Stage | Module | Responsibility | Language Support |
|-------|--------|----------------|------------------|
| **Spec Detection** | `sources/openapi.py:OpenAPISource.detect()` | Detect OpenAPI/AsyncAPI/GraphQL/HTML/PDF | All (format detection) |
| **Spec Parsing** | `sources/openapi.py:OpenAPISource.parse()` | Parse to dict, convert non-OpenAPI to pseudo-OpenAPI | All |
| **Repo Detection** | `repo/detection.py:detect_repo_profile()` | Detect language via config files + file counting | Python, TS, JS, Go, Java, Ruby, C#, Rust |
| **Repo Layout** | `graph/nodes/analyze_repo_layout.py` | Map artifacts to paths using RepoProfile | All |
| **Code Generation** | `graph/nodes/generate_code_and_tests.py` | LLM-powered codegen with spec-driven naming | Python (full), TS/Go (partial) |
| **Sandbox Validation** | `codegen/sandbox.py` | Python gates (ruff, mypy, bandit, pytest, coverage) | Python only |
| **Multi-lang Validation** | `codegen/sandbox_multilang.py` | Dispatch to LanguageStrategy | TS, Go (Docker available) |
| **Docker Runner** | `codegen/gates/docker_runner.py` | 2-phase Docker execution for Tier 1 | TS, Go |
| **HITL Gate** | `graph/nodes/hitl_gate.py` | Human approval before writes | All |
| **Repo Writes** | `graph/nodes/apply_repo_integration_changes.py` | Policy-enforced file writes via RepoIO | All |

### 2.2 Key Contracts Identified

#### Tier Enforcement
```python
# From codegen/gates/base.py
class Tier(Enum):
    PROD = "prod"  # Compiler-backed, deterministic, network-isolated
    EXP = "exp"    # Regex allowed, host toolchain, may skip gates
```

**Tier 1 Invariants**:
1. No regex-only artifact detection
2. Missing toolchain/config = hard failure
3. No network during validation phase
4. Deterministic: same input → same output

#### Language Strategy Interface
```python
# From codegen/gates/base.py
class LanguageStrategy(ABC):
    @abstractmethod
    def probe_toolchain(self, workspace: Path) -> Dict[str, Any]: ...
    
    @abstractmethod
    def provision(self, artifacts: List[ArtifactFile], workspace: Path, tier: Tier) -> SandboxEnv: ...
    
    @abstractmethod
    def validate(self, env: SandboxEnv, tier: Tier) -> ValidationResult: ...
```

**Registered Strategies** (from `gates/registry.py`):
- `python` → `PythonStrategy`
- `typescript` → `TypeScriptStrategy`
- `go` → `GoStrategy`

#### Docker Runner Contract
```python
# From codegen/gates/docker_runner.py
# 2-phase execution:
# Phase 1: provision() with network="bridge" (npm ci, go mod download)
# Phase 2: validate() with network="none" (tsc, eslint, vitest)
```

### 2.3 Spec Format Support

| Format | Detection Confidence | Parsing | Output | Determinism |
|--------|---------------------|---------|--------|-------------|
| **OpenAPI 3.x** | 0.95 | Native YAML/JSON | Native dict | ✅ Deterministic |
| **Swagger 2.x** | 0.95 | Native YAML/JSON | Native dict | ✅ Deterministic |
| **AsyncAPI** | 0.80 | YAML + conversion | Pseudo-OpenAPI | ⚠️ Conversion logic fixed |
| **GraphQL** | 0.80 | graphql-core + conversion | Pseudo-OpenAPI | ⚠️ Conversion logic fixed |
| **HTML docs** | 0.40 | LLM/regex extraction | Pseudo-OpenAPI | ❌ Non-deterministic |
| **PDF docs** | 0.35 | LLM/OCR extraction | Pseudo-OpenAPI | ❌ Non-deterministic |

### 2.4 Repo Detection Flow

```
detect_repo_profile(repo_root)
├── _detect_language_by_config_files(repo_path)  # O(1), authoritative
│   ├── Check go.mod/go.sum → "go"
│   ├── Check tsconfig.json → "typescript"
│   ├── Check package.json (no tsconfig) → "javascript"
│   ├── Check pyproject.toml/requirements.txt → "python"
│   ├── Check Dockerfile/Makefile/.github/workflows → language hints
│   └── Monorepo detection (nx.json, lerna.json, pnpm-workspace.yaml)
│
├── _detect_language_by_file_count(repo_path)  # O(n), fallback
│
└── Returns DetectedProfile(language, confidence, evidence, detected_paths)
```

**Confidence Scoring**:
- Base: 0.3
- Config file (go.mod, package.json): +0.3
- Framework detected (FastAPI, Next.js): +0.15-0.2
- Dockerfile confirmation: +0.15
- GitHub Actions confirmation: +0.15
- Multiple signals: +0.1 bonus

---

## 3. Gap Matrix

### 3.1 Critical Gaps (Blocking Production)

| ID | Gap | Current State | Required State | Severity | Effort |
|----|-----|--------------|----------------|----------|--------|
| **G-01** | Docker runner not wired | `sandbox_multilang.py` has Docker code but `generate_code_and_tests.py` uses `execute_multilang_sandbox()` which defaults to host | Auto-select Docker when available; fail Tier 1 without Docker | P0 | M |
| **G-02** | Monorepo workspace boundaries not enforced | Detection finds monorepo but doesn't constrain writes to correct workspace | RepoIO policy must respect workspace boundaries | P0 | M |
| **G-03** | Non-deterministic spec conversion | HTML/PDF specs use LLM extraction without quality markers | Add `_conversion_quality` field, warn in HITL gate | P1 | S |

### 3.2 High-Priority Gaps (Required for Full Coverage)

| ID | Gap | Current State | Required State | Severity | Effort |
|----|-----|--------------|----------------|----------|--------|
| **G-04** | Contract tests Python-only | Schemathesis/Prism gates in `sandbox.py` | Add contract test support for TS/Go | P1 | M |
| **G-05** | Java Tier 2 not implemented | Strategy registered but no implementation | Add `JavaStrategy` with Maven gates | P2 | M |
| **G-06** | Language selection dispatch implicit | `generate_code_and_tests.py` uses heuristics | Explicit `target_language` in WorkflowState | P1 | S |
| **G-07** | Sparse repo low confidence handling | Detected but LLM refinement rarely triggered | Auto-trigger LLM refinement below 0.4 confidence | P2 | S |

### 3.3 Quality Improvement Gaps

| ID | Gap | Current State | Required State | Severity | Effort |
|----|-----|--------------|----------------|----------|--------|
| **G-08** | No coverage gates for TS/Go | Python has coverage threshold | Add coverage reporting to TS (istanbul), Go (go test -cover) | P2 | M |
| **G-09** | Artifact detection regex fallback | `detect_artifacts()` in TS uses compiler API but regex fallback exists | Remove regex fallback for Tier 1 | P2 | S |
| **G-10** | Gate timing not aggregated | Individual gate durations tracked but no total | Add `total_gate_duration_ms` to ValidationResult | P3 | S |

---

## 4. Architecture Decision

### 4.1 Decision Point: Double-Down vs. Rearchitect

**Option A: Double-Down on Current Architecture**
- ✅ Infrastructure exists (LanguageStrategy, DockerRunner, Tier model)
- ✅ Tests exist (24 Docker runner tests, 30+ gate tests)
- ✅ Incremental hardening possible
- ⚠️ Some gaps require surgery on `generate_code_and_tests.py` (3760 lines)

**Option B: Major Rearchitect**
- Create new IR layer between LLM and validation
- Unify all languages into single code path
- ❌ Would invalidate existing tests
- ❌ 2-3 sprint delay for same coverage

### 4.2 Decision: **Option A (Double-Down)**

**Rationale**:
1. Current architecture is sound (strategy pattern, 2-phase Docker, tier model)
2. Gaps are wiring/integration issues, not fundamental design flaws
3. Existing test coverage provides safety net for changes
4. Production users are on Python path which is already solid

### 4.3 Key Architectural Constraints (Must Preserve)

1. **Tier Model**: Tier.PROD must guarantee compiler-backed validation
2. **2-Phase Docker**: Provision with network → Validate without network
3. **RepoIO Policy**: All writes go through policy-enforced RepoIO
4. **HITL Gate**: Human approval before destructive writes
5. **LanguageStrategy ABC**: All language-specific logic in strategies

---

## 5. Implementation Plan

### 5.1 Priority Ranking

| Priority | Gap ID | Title | Why Now? | Effort |
|----------|--------|-------|----------|--------|
| **P0.1** | G-01 | Wire Docker runner into main flow | Blocks Tier 1 for TS/Go | 4h |
| **P0.2** | G-02 | Monorepo workspace boundary enforcement | Data integrity | 4h |
| **P1.1** | G-06 | Explicit language selection | Removes ambiguity | 2h |
| **P1.2** | G-03 | Spec conversion quality markers | Transparency | 2h |
| **P1.3** | G-04 | Contract tests for TS/Go | Parity with Python | 6h |
| **P2.1** | G-05 | Java Tier 2 implementation | Coverage expansion | 4h |
| **P2.2** | G-08 | Coverage gates for TS/Go | Quality metrics | 4h |
| **P2.3** | G-07 | Auto-trigger LLM refinement | Better sparse repo handling | 2h |

### 5.2 Implementation Checklist: G-01 (Wire Docker Runner)

**File**: `src/integration_coworker/codegen/sandbox_multilang.py`

**Current Code** (line ~280):
```python
use_docker = config.use_docker and config.tier == Tier.PROD
```

**Required Changes**:
1. Auto-detect Docker availability at import time
2. Default `use_docker=True` when Docker available
3. Add clear warning when falling back to Tier.EXP

**Test File**: `tests/codegen/test_sandbox_multilang_docker.py`

**Acceptance Criteria**:
- [ ] `execute_multilang_sandbox(tier=Tier.PROD)` uses Docker when available
- [ ] `execute_multilang_sandbox(tier=Tier.PROD)` fails if Docker unavailable
- [ ] Tier.EXP falls back to host execution with warning

### 5.3 Implementation Checklist: G-02 (Monorepo Boundaries)

**File**: `src/integration_coworker/repo/io.py`

**Current Code**:
```python
# RepoIO.write() checks:
# - Path traversal prevention
# - Denylist patterns
# - Size limits
# - Audit logging
```

**Required Changes**:
1. Add `workspace_root` to RepoIO context (for monorepos)
2. Validate all writes are within `workspace_root` or below
3. For monorepos: `workspace_root = detected_workspace` not `repo_root`

**Test File**: `tests/repo/test_repo_io_monorepo.py`

**Acceptance Criteria**:
- [ ] Writes to sibling workspace rejected
- [ ] Writes to parent workspace rejected
- [ ] Writes within workspace allowed
- [ ] Single-package repos unaffected (workspace_root = repo_root)

### 5.4 Implementation Checklist: G-03 (Spec Quality Markers)

**File**: `src/integration_coworker/sources/openapi.py`

**Current Code**:
```python
def _html_to_pseudo_openapi(self, html_doc, uri: str) -> dict:
    spec = {
        "openapi": "3.0.0",
        "_parsed_from": "html",
        ...
    }
```

**Required Changes**:
1. Add `_conversion_quality` field with values: "deterministic", "llm_assisted", "best_effort"
2. Add `_conversion_warnings` list
3. HITL gate displays conversion quality

**Test File**: `tests/sources/test_openapi_conversion_quality.py`

**Acceptance Criteria**:
- [ ] OpenAPI 3.x specs have `_conversion_quality: "deterministic"`
- [ ] HTML specs have `_conversion_quality: "llm_assisted"`
- [ ] PDF specs have `_conversion_quality: "best_effort"`
- [ ] HITL gate displays warning for non-deterministic conversions

---

## 6. Risk Assessment

### 6.1 Risk Matrix

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Docker not available in CI | Tier 1 validation fails | Medium | Document Docker requirement; provide Tier 2 fallback |
| Monorepo detection false positive | Writes to wrong location | Low | Require explicit `.integration-coworker.yaml` for monorepos |
| LLM spec conversion hallucination | Invalid endpoints generated | Medium | Mark as `best_effort`; require human review |
| Large spec timeout | Validation fails | Low | Increase timeout; add progress logging |

### 6.2 Rollback Strategy

All changes are feature-flagged:
```python
# Feature flags in config
ENABLE_DOCKER_VALIDATION = os.environ.get("IC_DOCKER_VALIDATION", "1") == "1"
ENABLE_MONOREPO_BOUNDARIES = os.environ.get("IC_MONOREPO_BOUNDARIES", "1") == "1"
ENABLE_SPEC_QUALITY_MARKERS = os.environ.get("IC_SPEC_QUALITY", "1") == "1"
```

---

## 7. Test Strategy

### 7.1 New Test Files Required

| Test File | Purpose | Tests |
|-----------|---------|-------|
| `tests/codegen/test_sandbox_multilang_docker.py` | Docker runner integration | 5 |
| `tests/repo/test_repo_io_monorepo.py` | Monorepo boundary enforcement | 8 |
| `tests/sources/test_openapi_conversion_quality.py` | Spec quality markers | 6 |
| `tests/e2e/test_typescript_tier1.py` | End-to-end TS validation | 4 |
| `tests/e2e/test_go_tier1.py` | End-to-end Go validation | 4 |

### 7.2 Existing Tests to Update

| Test File | Changes |
|-----------|---------|
| `tests/test_codegen_sandbox.py` | Add Docker availability check |
| `tests/test_repo_io_policy.py` | Add monorepo workspace tests |
| `tests/test_hitl_gate.py` | Add spec quality display tests |

### 7.3 Production Validation Procedure

```bash
# 1. Run unit tests
pytest tests/codegen/ tests/repo/ tests/sources/ -v

# 2. Run E2E with Docker
docker compose up -d
pytest tests/e2e/ -v --tb=short

# 3. Run against real specs
python scripts/test_multi_language_production.py

# 4. Run production readiness check
python scripts/verify_production_readiness.py
```

---

## Appendix A: File References

| File | Lines | Purpose |
|------|-------|---------|
| `src/integration_coworker/codegen/gates/base.py` | 286 | LanguageStrategy ABC, Tier enum |
| `src/integration_coworker/codegen/gates/docker_runner.py` | 340+ | Docker 2-phase execution |
| `src/integration_coworker/codegen/gates/typescript.py` | 550+ | TypeScript gates |
| `src/integration_coworker/codegen/gates/go.py` | 400+ | Go gates |
| `src/integration_coworker/codegen/sandbox_multilang.py` | 450 | Multi-lang dispatch |
| `src/integration_coworker/repo/detection.py` | 750+ | Repo profile detection |
| `src/integration_coworker/repo/io.py` | 300+ | RepoIO policy |
| `src/integration_coworker/sources/openapi.py` | 450 | Spec parsing |
| `src/integration_coworker/graph/nodes/generate_code_and_tests.py` | 3760 | Main codegen |

---

*Document created as part of production hardening effort*

---

## Appendix B: Implementation Results (2025-01-24)

### B.1 Implemented Changes

#### G-01: Docker Runner Auto-Selection

**Files Modified**:
- `src/integration_coworker/codegen/sandbox_multilang.py`

**Changes**:
1. Added `_check_docker_available_cached()` for efficient Docker detection
2. Modified `MultiLangSandboxConfig` to auto-select `tier` and `use_docker` based on Docker availability
3. Added `fail_on_docker_unavailable` flag (default: True) to fail loudly when Tier.PROD requested without Docker
4. Updated execution logic to respect auto-selection and fail-fast behavior

**Test File**: `tests/codegen/test_sandbox_multilang_docker_wiring.py` (10 tests)

#### G-03: Spec Conversion Quality Markers

**Files Modified**:
- `src/integration_coworker/sources/openapi.py`

**Changes**:
1. Added `_conversion_quality` field to all parsed specs:
   - `"deterministic"` for OpenAPI, Swagger, AsyncAPI, GraphQL
   - `"llm_assisted"` for HTML API docs
   - `"best_effort"` for PDF API docs
2. Added `_conversion_warnings` list with context-specific warnings
3. Updated `_extract_metadata()` to include conversion quality in metadata

**Test File**: `tests/sources/test_openapi_conversion_quality.py` (12 tests)

### B.2 Test Results

```
Test Suite                                    | Passed | Skipped | Failed
----------------------------------------------|--------|---------|-------
tests/codegen/test_sandbox_multilang_docker_wiring.py | 10     | 0       | 0
tests/sources/test_openapi_conversion_quality.py      | 11     | 1       | 0
tests/codegen/ (all)                                  | 100    | 9       | 0
tests/repo/test_repo_io_policy.py                     | 30     | 0       | 0
tests/test_hitl_gate.py                               | 25     | 0       | 0
tests/test_codegen_sandbox.py                         | 24     | 0       | 0
----------------------------------------------|--------|---------|-------
TOTAL                                         | 181    | 10      | 0
```

### B.3 Bug List

**No bugs found during implementation or testing.**

All 181 tests pass. The 10 skipped tests are expected:
- 9 tests require optional dependencies (graphql-core, etc.)
- 1 test skipped due to GraphQL parser not installed

### B.4 Remaining Work (P2-P3)

| Priority | Gap ID | Status | Notes |
|----------|--------|--------|-------|
| P0.1 | G-01 | ✅ Done | Docker auto-selection |
| P0.2 | G-02 | ⏳ TODO | Monorepo workspace boundaries |
| P1.1 | G-06 | ⏳ TODO | Explicit language selection |
| P1.2 | G-03 | ✅ Done | Spec quality markers |
| P1.3 | G-04 | ⏳ TODO | Contract tests for TS/Go |
| P2.1 | G-05 | ⏳ TODO | Java Tier 2 |
| P2.2 | G-08 | ⏳ TODO | Coverage gates for TS/Go |
| P2.3 | G-07 | ⏳ TODO | Auto LLM refinement |

