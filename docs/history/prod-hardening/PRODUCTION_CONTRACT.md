# Production Contract: Tier.PROD, Tier.EXP, and HITL

This document defines the contracts for production-grade code generation and validation.

## Overview

The integration coworker supports three operational tiers that control validation strictness
and human oversight:

| Tier | Docker Required | Network Isolation | Lockfile | HITL Gate |
|------|-----------------|-------------------|----------|-----------|
| **Tier.PROD** | Yes | `--network none` in validate | Required | Auto-pause (default) |
| **Tier.EXP** | No | Host toolchain | Optional | Same as PROD |
| **HITL bypass** | Either | Either | Either | Skipped |

---

## Tier.PROD (Production)

**Purpose**: Compiler-backed validation with full determinism guarantees.

### Requirements

1. **Docker Daemon Available**: `docker ps` must succeed
2. **Network Isolation**: 
   - Provision phase: `--network bridge` (dependency download)
   - Validate phase: `--network none` (isolation)
3. **Deterministic Installs**:
   - TypeScript: `npm ci` (not `npm install`) - requires exact lockfile match
   - Go: `go mod download` with `go.sum` verification
4. **Lockfile Required**:
   - TypeScript: `package-lock.json` or `pnpm-lock.yaml` must exist
   - Go: `go.mod` (and `go.sum` for full verification)

### Guarantees

- ✅ All validation is compiler-backed (no regex heuristics)
- ✅ No network access during validation (no runtime dependency downloads)
- ✅ Identical inputs produce identical outputs
- ✅ Fail-fast on missing toolchain (no silent degradation)

### Code Location

```python
# src/integration_coworker/codegen/gates/docker_runner.py

# TypeScript provision (MUST use npm ci):
provision_cmd = ["npm", "ci"]  # NOT npm install

# Go provision:
provision_cmd = ["go", "mod", "download"]

# Validate with network disabled:
result = self._run_container(
    image=image,
    workspace=workspace,
    command=gate_cmd,
    network="none",  # Network DISABLED for validation
    timeout=self.config.gate_timeout,
)
```

---

## Tier.EXP (Experimental)

**Purpose**: Fast iteration using host toolchain, accepts less strict validation.

### Requirements

1. **Host Toolchain**: Node.js, Go, etc. must be installed locally
2. **No Docker Required**: Runs directly on host
3. **Lockfile Optional**: `npm install` allowed (not `npm ci`)

### Trade-offs

- ⚠️ May use regex-based checks (marked `UNTRUSTED` in results)
- ⚠️ Gates may be skipped if tooling unavailable
- ⚠️ Network access allowed during validation
- ⚠️ Non-deterministic dependency resolution

### When to Use

- Local development iteration
- Testing without Docker setup
- Quick feedback loops

### Code Location

```python
# src/integration_coworker/codegen/gates/base.py

class Tier(Enum):
    PROD = "prod"  # Compiler-backed, no regex, fail on missing toolchain
    EXP = "exp"    # Regex allowed, may skip gates, marked UNTRUSTED
```

---

## HITL (Human-in-the-Loop)

### Purpose

Production workflows pause at a review gate before:
- Writing files to the repository
- Persisting to the knowledge graph

### Modes

#### Option A: `hitl_mode` Flag (Production Implementation)

```python
# src/integration_coworker/api/types.py

@dataclass
class IntegrationOptions:
    # V3.0: HITL mode control for production and testing
    # - "auto": Skip HITL in API calls (current behavior, non-interactive)
    # - "always": Always require HITL approval (for UI flows)
    # - "never": Skip HITL (CI/tests only, EXPLICIT bypass)
    hitl_mode: Literal["auto", "always", "never"] = "auto"
    
    # Legacy (backwards compat, deprecated): Use hitl_mode="never" instead
    skip_hitl: bool = False
```

**Semantics** (as implemented in `should_skip_hitl()`):

| Mode | skip_hitl | Behavior | Use Case |
|------|-----------|----------|----------|
| `"auto"` | False | **Skips HITL** (default for CI safety) | API calls via SDK |
| `"always"` | False | **Requires approval** | Interactive UI flows |
| `"never"` | False | **Skips HITL** (explicit bypass) | CI/E2E tests |
| Any | True | **Skips HITL** (deprecated path) | Legacy compatibility |

**WARNING**: `"auto"` currently behaves like `"never"` - it skips HITL for API calls.
This is intentional for CI safety. Use `"always"` if you want to force HITL approval.

**Usage**:
```python
result = design_and_generate_integration(
    spec_refs=["spec.yaml"],
    task_description="Add user endpoint",
    options=IntegrationOptions(hitl_mode="never"),  # CI/test mode
)
```

#### Option B: Resume Semantics with CLI Commands (Implemented)

For true HITL workflows, the system provides:
1. Pause at HITL gate with `status="needs_approval"`
2. Return a `resume_token` 
3. Human reviews via CLI: `integration-coworker hitl-status <run_id>`
4. Human approves/rejects: `integration-coworker hitl-resume <run_id> --approve`
5. Workflow resumes from checkpoint

**CLI Commands** (implemented in `src/integration_coworker/cli.py`):
```bash
# Check status
integration-coworker hitl-status run_abc123

# Approve and resume
integration-coworker hitl-resume run_abc123 --approve

# Reject
integration-coworker hitl-resume run_abc123 --reject
```

### E2E Test Requirements

For E2E tests that exercise the full pipeline:

1. **MUST use `hitl_mode="never"`** to avoid test hangs
2. **MUST NOT weaken Tier.PROD guarantees** (e.g., changing npm ci → npm install)
3. **MUST document that HITL is bypassed** in test docstrings

```python
@pytest.mark.e2e
def test_full_pipeline():
    """
    E2E test: Full pipeline with HITL bypassed.
    
    WARNING: Uses hitl_mode="never" - not representative of production HITL flow.
    """
    result = design_and_generate_integration(
        spec_refs=[spec_path],
        task_description="Test task",
        repo_root=repo_path,
        options=IntegrationOptions(
            repo_integration_enabled=True,
            hitl_mode="never",  # CI/test automation (production contract)
        ),
    )
```

---

## Lockfile Correctness

### Problem

`npm ci` requires an **exact match** between `package.json` and `package-lock.json`.
If the lockfile is stale or manually edited, `npm ci` fails.

### Solutions (Ranked)

#### 1. Generate Lockfile in Provision Phase (Recommended)

For E2E tests that generate code dynamically:

```python
# Provision phase (network allowed):
# 1. Write package.json
# 2. Run "npm install" to generate package-lock.json
# 3. Run "npm ci" to verify lockfile validity

# This is acceptable because provision phase allows network
```

#### 2. Use Real Fixture with Valid Lockfile

For reproducible tests, store fixtures with valid lockfiles:

```
tests/fixtures/typescript/simple-project/
├── package.json
├── package-lock.json  # Generated via npm install, committed to repo
├── tsconfig.json
└── src/
    └── index.ts
```

#### 3. Use pnpm (Alternative)

pnpm's `--frozen-lockfile` is more forgiving about minor mismatches.

---

## Validation Matrix

| Scenario | Tier | Docker | Lockfile | HITL | Files Written |
|----------|------|--------|----------|------|---------------|
| Production run | PROD | ✅ | ✅ Required | ⏸️ Pauses | After approval |
| CI E2E test | PROD | ✅ | ✅ Required | ⏭️ skip_hitl | To temp dir |
| Local dev | EXP | ❌ | ⚠️ Optional | ⏸️ Pauses | After approval |
| Quick test | EXP | ❌ | ⚠️ Optional | ⏭️ skip_hitl | To temp dir |

---

## Non-Negotiables

1. **Tier.PROD MUST use `npm ci`**, never `npm install`
2. **Tier.PROD validate phase MUST use `--network none`**
3. **E2E tests MUST NOT weaken production semantics to pass**
4. **If tests fail due to lockfile issues, fix the lockfile, not the runner**
5. **skip_hitl is for tests only, not production workflows**

---

## Files Reference

| File | Purpose |
|------|---------|
| `src/integration_coworker/codegen/gates/base.py` | Tier enum, base classes |
| `src/integration_coworker/codegen/gates/docker_runner.py` | Docker-based Tier.PROD execution |
| `src/integration_coworker/api/types.py` | `IntegrationOptions.skip_hitl` |
| `src/integration_coworker/cli.py` | `hitl-status`, `hitl-resume` commands |
| `tests/e2e/` | E2E tests with Docker + Postgres |
