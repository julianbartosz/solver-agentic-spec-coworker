# ADR-0010: Production Testing Architecture

**Status**: Accepted  
**Date**: 2025-01-17  
**Author**: Auto-generated from production audit  
**Supersedes**: None  

---

## Context

The Integration Co-Worker project has accumulated 3352 tests across multiple test categories, with ~20 markers defining various test profiles. The production testing infrastructure has grown organically, resulting in:

1. **Scattered documentation** - Multiple docs reference gaps without single source of truth
2. **No unified test matrix** - Commands spread across Makefile, CI, scripts
3. **Inconsistent environment handling** - Different tests expect different env vars
4. **Manual-only production validation** - No automated bug hunt tool existed

### Evidence-Based Gap Analysis

| Gap ID | Description | Severity | Evidence Source |
|--------|-------------|----------|-----------------|
| GAP-T1 | No automated bug-finding script | High | TESTING_AND_OBSERVABILITY_AUDIT.md §3.1 |
| GAP-T2 | Redis cache tests use fakeredis only | Medium | PRODUCTION_TESTING_GUIDE.md §Known Gaps |
| GAP-T3 | LangSmith traces not validated | Low | DEMO_HARNESS_IMPROVEMENTS.md §476 |
| GAP-T4 | TypeScript/Go type validation missing | Medium | PRODUCTION_TESTING_GUIDE.md §460 |
| GAP-T5 | Prod smoke tests secrets-gated | Medium | ci.yml (prod-smoke job) |

---

## Decision

We adopt a **tiered testing architecture** with explicit boundaries:

### Tier 1: Fast Unit Tests (Blocking CI Gate)
- **Marker**: `-m "not (postgres or e2e or slow or docker)"`
- **Environment**: `USE_SQLITE=true USE_MOCK_LLM=true`
- **Runtime Target**: < 60 seconds
- **Coverage Gate**: 60% minimum

### Tier 2: Integration Tests (Blocking CI Gate)
- **Marker**: `-m "integration and not integration_live"`
- **Environment**: `USE_SQLITE=true USE_MOCK_LLM=true`
- **Runtime Target**: < 10 minutes
- **Infrastructure**: No external dependencies

### Tier 3: E2E Tests (Blocking CI Gate, Docker Required)
- **Marker**: `-m "e2e and docker"`
- **Environment**: Testcontainers for Postgres
- **Runtime Target**: < 20 minutes
- **Infrastructure**: Docker daemon required

### Tier 4: Prod Smoke (Manual Dispatch, Secrets Required)
- **Marker**: `-m prod_smoke`
- **Environment**: `OPENAI_API_KEY` required
- **Runtime Target**: < 15 minutes
- **Infrastructure**: Real Postgres + Real LLM

### Tier 5: Live Integration (Manual, Host Allowlist Required)
- **Marker**: `-m integration_live`
- **Environment**: `ALLOW_LIVE=1 LIVE_HOST_ALLOWLIST=...`
- **Runtime Target**: < 20 minutes
- **Infrastructure**: Network access to specified hosts

---

## Alternatives Considered

### Alternative A: Single Unified Test Suite
**Approach**: Run all tests in single CI job with optional deps.

**Pros**:
- Simpler CI configuration
- Single pass/fail signal

**Cons**:
- Long CI times (>30 min)
- Secrets required for all runs
- Can't isolate failures

**Decision**: Rejected - violates fast feedback principle

### Alternative B: Marker-Per-Feature
**Approach**: Create markers per feature (e.g., `kg`, `codegen`, `sandbox`).

**Pros**:
- Fine-grained test selection
- Can run subset for specific changes

**Cons**:
- Marker explosion (already have 20+)
- Maintenance burden
- Cross-feature tests ambiguous

**Decision**: Rejected - complexity exceeds value

### Alternative C: Tiered Architecture (Chosen)
**Approach**: Organize tests by infrastructure requirement level.

**Pros**:
- Clear boundaries
- Predictable CI times
- Incremental validation
- Easy to explain

**Cons**:
- Some tests span tiers
- Requires discipline

**Decision**: Accepted - best balance of clarity and practicality

---

## Implementation

### 1. Documentation Artifacts

| Document | Purpose | Location |
|----------|---------|----------|
| PRODUCTION_TEST_MATRIX.md | Exact commands per tier | `docs/` |
| PRODUCTION_TESTING_GUIDE.md | Comprehensive guide | `docs/` |
| This ADR | Architectural decision | `docs/decisions/` |

### 2. CI Pipeline Structure

```yaml
jobs:
  lint:          # Always runs, gates all others
  unit-fast:     # Tier 1, Python matrix
  integration:   # Tier 2, Python matrix
  e2e:           # Tier 3, Docker required
  record:        # Optional, cassette refresh
  live:          # Optional, allowlist required
  prod-smoke:    # Optional, secrets required
```

### 3. Bug Hunt Automation

Created `scripts/bug_hunt.py` providing:
- Tiered test execution (unit → e2e)
- JSON/Markdown report output
- Automatic environment safety defaults
- Clear command visibility

### 4. Environment Variable Matrix

| Variable | Tier 1/2 | Tier 3 | Tier 4/5 |
|----------|----------|--------|----------|
| `USE_SQLITE` | true | - | false |
| `USE_MOCK_LLM` | true | true | false |
| `DATABASE_URL` | - | auto | required |
| `OPENAI_API_KEY` | - | - | required |
| `VALIDATION_PROFILE` | offline | offline | live |

---

## Consequences

### Positive
1. **Clear test categories** - Engineers know which tier to run
2. **Fast CI feedback** - Blocking tests < 15 min total
3. **Safe defaults** - No network/secrets without explicit opt-in
4. **Automated bug hunt** - Single command discovers regressions

### Negative
1. **Multiple commands** - Can't "run everything" simply
2. **Documentation burden** - Matrix must stay updated
3. **Tier boundary ambiguity** - Some tests don't fit cleanly

### Mitigations
- `scripts/bug_hunt.py` provides "run everything" equivalent
- CI badges show tier status
- Quarterly review of test categorization

---

## Related Documents

- `docs/PRODUCTION_TEST_MATRIX.md` - Exact commands
- `docs/PRODUCTION_TESTING_GUIDE.md` - Full guide
- `docs/TESTING_AND_OBSERVABILITY_AUDIT.md` - Original gap analysis
- `.github/workflows/ci.yml` - CI implementation

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-01-17 | Auto-generated | Initial ADR created from production audit |
