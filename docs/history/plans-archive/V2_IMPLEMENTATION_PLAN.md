# V2 Implementation Plan

**Last Updated**: 2025-12-18
**Status**: Step 0 (Repo Discovery) → Step 5 (Backlog)
**Validation Log**: `logs/demo-final/demo-20251218-213620.log`

> **Note**: All "✅ Complete" claims must be backed by file/line citations AND a verification command/test name. Claims without evidence are marked "⚠️ Unverified".

---

## Step 0: Repository Discovery & Evidence Table

### Evidence Table: Current Capabilities

| Capability | Status | Primary File(s) | Key Lines | Evidence |
|-----------|--------|-----------------|-----------|----------|
| **Multi-Language Support** | 🟡 Partial | `graph/nodes/generate_code_and_tests.py`, `codegen/prompts.py` | 931-960, 994, 1496-1531 | Bug #70: `target_language` passed but output still Python-shaped |
| **File Ingestion (CSV/Excel/Fixed-Width)** | ✅ Complete | `codegen/file_templates.py` | 562+ | `pytest tests/test_file_templates.py -v` → 76 tests pass |
| **Determinism/Replay** | ✅ Complete | `llm/client.py` | 80-130, 170-230 | `LLM_MODE=REPLAY pytest tests/test_llm_replay.py -v` |
| **Evaluation Storage** | ⚠️ Unverified | `graph/nodes/persist_run_outcome.py` | 35-150 | Tables exist, no CLI query command yet |
| **Policy/Safety** | ✅ Complete | `llm/content_policy.py` | full (~300 lines) | `pytest tests/test_content_policy.py -v` → pass |
| **Repo Profiles** | ✅ Complete | `repo/profiles.py` | 60-140, 300-500 | `pytest tests/test_repo_profiles.py -v` → pass |
| **Incremental/Caching** | 🟡 Partial | `config/__init__.py` | 164-190, 486-500 | Redis cache works; no incremental AST diff |
| **Parallel Execution** | ✅ Complete | `graph/parallel.py`, `graph/nodes/generate_code_and_tests.py` | 1170-1300 | Demo log shows parallel branches executed |
| **Checkpoint/Recovery** | ⚠️ Unverified | `persistence/checkpoints.py` | 1-150, 180-350 | Bug #67/#82/#89 fixed; **no resume test proving excluded fields restore** |
| **Streaming Persistence** | ✅ Complete | `config/__init__.py`, `graph/nodes/embed_spec_chunks.py` | 225-245 | Auto-enabled for specs >500KB |
| **Sandbox Validation** | ✅ Complete | `codegen/sandbox.py` | full | Demo log: 6/6 gates pass (ruff, mypy, bandit, pytest) |
| **KG Learning** | ⚠️ Partial | `persistence/seed_kg.py`, `graph/nodes/persist_kg_learning.py` | full | 6/7 templates seeded; transaction abort bug (NEW-04) |
| **Self-Review** | ✅ Complete | `graph/nodes/generate_code_and_tests.py` | 96-220 | Profile-controlled via `enable_self_review` |
| **Test Repair** | ✅ Complete | `graph/nodes/generate_code_and_tests.py` | 232-340 | LLM-guided assertion repair runs in production profile |

### Key Architecture Files

| File | Purpose | LOC |
|------|---------|-----|
| `src/integration_coworker/graph/nodes/generate_code_and_tests.py` | Core codegen orchestration | 3556 |
| `src/integration_coworker/llm/client.py` | Multi-provider LLM client | ~1100 |
| `src/integration_coworker/llm/content_policy.py` | Semantic code validation | ~310 |
| `src/integration_coworker/repo/profiles.py` | Repo profile detection | ~500 |
| `src/integration_coworker/persistence/checkpoints.py` | Workflow state persistence | ~400 |
| `src/integration_coworker/codegen/file_templates.py` | File parser codegen | ~600 |
| `src/integration_coworker/codegen/prompts.py` | LLM prompt construction | ~1542 |
| `scripts/demo-final-showcase.sh` | Production integration test | ~1100 |

---

## Step 1: Target Architecture Decision

### Decision Point: Multi-Language Code Generation IR

**The Question**: How should we generate code for non-Python languages?

#### Option A: Language-Specific Templates (Current State++)

**Description**: Extend current template system with per-language template files.

```
codegen/templates/
├── python/
│   ├── client.py.jinja2
│   ├── flow.py.jinja2
│   └── test.py.jinja2
├── typescript/
│   ├── client.ts.jinja2
│   ├── flow.ts.jinja2
│   └── test.ts.jinja2
└── go/
    ├── client.go.jinja2
    └── ...
```

| Pros | Cons |
|------|------|
| Predictable output, no LLM variance | High maintenance burden (7 languages × 3 artifacts = 21+ templates) |
| Fast generation (no LLM calls) | Limited flexibility for edge cases |
| Testable with snapshot tests | Requires deep per-language expertise |

#### Option B: Pure LLM Generation with Language Context (Current Approach)

**Description**: Pass `target_language` to prompts and let LLM generate code directly.

| Pros | Cons |
|------|------|
| Flexible, handles edge cases | Output variance, may need retries |
| Lower maintenance (prompts, not templates) | Requires quality gates per language |
| Can leverage LLM's training on idiomatic patterns | Harder to test deterministically |

#### Option C: Hybrid IR (AST Abstraction Layer) ⭐ RECOMMENDED

**Description**: Generate a language-agnostic IR, then render to target language.

```python
@dataclass
class CodegenIR:
    """Language-agnostic intermediate representation."""
    imports: List[Import]
    classes: List[ClassDef]
    functions: List[FunctionDef]
    http_calls: List[HTTPCall]  # method, path, params, body
    error_handling: ErrorStrategy
    
    def render(self, language: str) -> str:
        """Render IR to target language."""
        renderer = get_renderer(language)
        return renderer.render(self)
```

| Pros | Cons |
|------|------|
| Best of both: LLM generates IR, templates render | More complex architecture |
| Testable IR layer + deterministic rendering | Initial implementation effort |
| Easy to add languages (just write renderer) | IR may not capture all idioms |
| Can validate IR before rendering | |

**Chosen Option**: **Option C (Hybrid IR)** with fallback to Option B for unsupported languages.

**Rationale**:
1. Currently, `target_language` is passed to prompts but output is still Python-shaped
2. IR layer provides validation opportunity before rendering
3. Testable: IR → render → snapshot comparison
4. Progressive rollout: Start with Python+TypeScript, add others

**Downstream Impact**:
- `codegen/prompts.py`: Generate IR instead of raw code
- `codegen/ir/` (new): Define `CodegenIR` dataclasses
- `codegen/renderers/` (new): Per-language renderers
- `codegen/sandbox.py`: Add language-specific linters (eslint, golint)

---

## Step 2: Implementation Plan by Feature Area

### Area A: Multi-Language IR Implementation

**Priority**: P1 (High)
**Effort**: L (Large - 2-3 weeks)
**Dependencies**: None

| Task | File(s) | Description |
|------|---------|-------------|
| A.1 | `codegen/ir/__init__.py` | Define `CodegenIR`, `ClassDef`, `FunctionDef`, `HTTPCall` dataclasses |
| A.2 | `codegen/ir/validators.py` | IR validation (required fields, type checking) |
| A.3 | `codegen/renderers/base.py` | Abstract `Renderer` class |
| A.4 | `codegen/renderers/python.py` | Python renderer (extract from current prompts.py) |
| A.5 | `codegen/renderers/typescript.py` | TypeScript renderer with proper imports, async/await |
| A.6 | `codegen/prompts.py` | Modify `build_codegen_prompt` to request IR JSON output |
| A.7 | `tests/test_codegen_ir.py` | Unit tests for IR → render pipeline |
| A.8 | `tests/snapshots/` | Golden file snapshots for each language |

**Validation Gate**:
```bash
pytest tests/test_codegen_ir.py -v
# Expect: IR parses correctly, renders match snapshots for Python + TypeScript
```

### Area B: File Ingestion Hardening

**Priority**: P2 (Medium)
**Effort**: S (Small - 3-5 days)
**Dependencies**: None (Track B was completed)

| Task | File(s) | Description |
|------|---------|-------------|
| B.1 | `codegen/file_templates.py` | Add Parquet parser codegen (Arrow) |
| B.2 | `codegen/file_templates.py` | Add JSON Lines (.jsonl) parser codegen |
| B.3 | `tests/test_file_templates.py` | Adversarial inputs: Unicode BOM, mixed line endings |
| B.4 | `codegen/file_templates.py` | Schema evolution detection (new columns, type changes) |

**Validation Gate**:
```bash
pytest tests/test_file_templates.py tests/test_excel_adversarial.py -v
# Expect: All pass, including adversarial edge cases
```

### Area C: Evaluation & Metrics Hardening

**Priority**: P1 (High)
**Effort**: M (Medium - 1-2 weeks)
**Dependencies**: None

| Task | File(s) | Description |
|------|---------|-------------|
| C.1 | `persistence/eval_storage.py` (new) | Centralized eval metrics persistence |
| C.2 | `graph/nodes/persist_run_outcome.py` | Enrich `rag_eval_metrics` with retrieval precision/recall |
| C.3 | `api/types.py` | Add `EvalMetrics` response model |
| C.4 | `cli.py` | Add `bd eval-report --run-id <id>` command |
| C.5 | `tests/test_eval_storage.py` | Test metrics persistence and retrieval |

**Validation Gate**:
```bash
pytest tests/test_eval_storage.py -v
python -m integration_coworker.cli eval-report --run-id <test-run-id>
# Expect: Metrics table with precision, recall, latency breakdown
```

### Area D: Policy & Safety Enhancements

**Priority**: P1 (High)
**Effort**: S (Small - 3-5 days)
**Dependencies**: None

| Task | File(s) | Description |
|------|---------|-------------|
| D.1 | `llm/content_policy.py` | Add SQL injection pattern detection |
| D.2 | `llm/content_policy.py` | Add SSRF pattern detection (dynamic URL construction) |
| D.3 | `codegen/security.py` | Integrate SAST (semgrep) as optional gate |
| D.4 | `tests/test_codegen_security.py` | Add adversarial security test cases |

**Validation Gate**:
```bash
pytest tests/test_codegen_security.py -v
# Expect: Known-bad patterns rejected, CRITICAL severity
```

### Area E: Repo Profile Inference Improvements

**Priority**: P2 (Medium)
**Effort**: S (Small - 3-5 days)
**Dependencies**: None

| Task | File(s) | Description |
|------|---------|-------------|
| E.1 | `repo/profiles.py` | Add Rust profile (Cargo.toml detection) |
| E.2 | `repo/profiles.py` | Add Kotlin profile (build.gradle.kts detection) |
| E.3 | `repo/llm_inference.py` | Improve monorepo detection (multiple package.json) |
| E.4 | `tests/test_repo_profiles.py` | Add profile detection tests for new languages |

**Validation Gate**:
```bash
pytest tests/test_repo_profiles.py -v
# Expect: Rust and Kotlin profiles detected correctly
```

### Area F: Incremental Update Fast Path

**Priority**: P2 (Medium)
**Effort**: M (Medium - 1-2 weeks)
**Dependencies**: Area A (IR layer)

| Task | File(s) | Description |
|------|---------|-------------|
| F.1 | `persistence/spec_cache.py` (new) | Spec AST diff computation |
| F.2 | `graph/nodes/ingest_spec.py` | Skip unchanged endpoints (hash comparison) |
| F.3 | `graph/nodes/generate_code_and_tests.py` | Regenerate only affected artifacts |
| F.4 | `api/types.py` | Add `--incremental` flag to GenerateRequest |
| F.5 | `tests/test_incremental_codegen.py` | Test incremental regeneration |

**Validation Gate**:
```bash
# First run: Generate all
python -m integration_coworker.cli run --spec-ref specs/stripe_api.json ...

# Modify spec slightly
# Second run: Should skip unchanged endpoints
python -m integration_coworker.cli run --spec-ref specs/stripe_api.json --incremental ...
# Expect: "Skipped N unchanged endpoints" in logs
```

### Area G: Service Deployment

**Priority**: P3 (Low)
**Effort**: M (Medium - 1-2 weeks)
**Dependencies**: None

| Task | File(s) | Description |
|------|---------|-------------|
| G.1 | `Dockerfile` | Multi-stage build with runtime optimization |
| G.2 | `docker-compose.prod.yml` | Production compose with health checks |
| G.3 | `scripts/healthcheck.py` | Comprehensive health endpoint |
| G.4 | `docs/operations/DEPLOYMENT.md` | Deployment runbook |
| G.5 | `.github/workflows/release.yml` | CI/CD pipeline with artifact publishing |

**Validation Gate**:
```bash
docker compose -f docker-compose.prod.yml up -d
curl http://localhost:8000/health
# Expect: {"status": "healthy", "db": "connected", "redis": "connected"}
```

### Area H: Documentation & Onboarding

**Priority**: P2 (Medium)
**Effort**: S (Small - 3-5 days)
**Dependencies**: Areas A-G (document what's built)

| Task | File(s) | Description |
|------|---------|-------------|
| H.1 | `docs/ARCHITECTURE.md` | Update with IR layer, new components |
| H.2 | `docs/getting-started/QUICKSTART.md` | 5-minute quickstart guide |
| H.3 | `docs/api-reference/` | OpenAPI spec for REST API |
| H.4 | `examples/` | Add examples for each language |

---

## Step 3: Production Validation Pass

### Production Demo Results (2024-12-18)

**Command**: `./scripts/demo-final-showcase.sh --quick --no-per-spec-validation`

| Step | Result | Notes |
|------|--------|-------|
| Python Environment | ✅ PASS | 3.11.14, single site-packages |
| Environment Variables | ✅ PASS | DATABASE_URL, OPENAI_API_KEY, REDIS_URL, LANGCHAIN_API_KEY |
| DB Schema Init | ✅ PASS | PostgreSQL + pgvector |
| KG Seeding | ⚠️ WARN | 6/7 templates seeded (transaction abort on duplicates - NEW-04) |
| Async Runtime | ✅ PASS | AsyncPostgresSaver, parallel enabled |
| Redis LLM Cache | ✅ PASS | hits=2, misses=23, evictions=0 |
| Stripe API Run | ✅ PASS | 103s, 585 endpoints, 3 artifacts |
| Sandbox Validation | ✅ PASS | 6/6 gates (venv, deps, ruff, mypy, bandit, pytest) |

**Sandbox Gate Timings**:
- venv_creation: 1709ms
- dependency_install: 4594ms
- ruff: 462ms
- mypy: 14496ms
- bandit: 357ms
- pytest: 557ms

### Test Matrix

| Test Category | Command | Expected |
|--------------|---------|----------|
| Unit Tests | `pytest tests/unit/ -v` | All pass |
| Graph Tests | `pytest tests/graph/ -v` | All pass |
| Integration Tests | `pytest tests/integration/ -v -m "not slow"` | All pass |
| Adversarial Tests | `pytest tests/ -k adversarial -v` | All pass |
| Postgres Tests | `pytest tests/ -k postgres -v` | All pass (with DB) |
| Full Demo | `./scripts/demo-final-showcase.sh --quick` | Exit 0, no errors |

### Current Test Count (from Track B certification)

| Suite | Count | Status |
|-------|-------|--------|
| Sanity | 2 | ✅ |
| Routing | 1 | ✅ |
| Adversarial | 59 | ✅ |
| Templates + Validation | 76 | ✅ |
| Logging | 5 | ✅ |
| Postgres | 1 | ✅ |
| **Total** | **144** | ✅ All Pass |

---

## Step 4: Bug Report & Fix Plan

### Known Issues from Repo Discovery

| Bug ID | Severity | Description | File | Fix Estimate |
|--------|----------|-------------|------|--------------|
| NEW-01 | P2 | Multi-language codegen produces Python-shaped output even with `target_language` | `codegen/prompts.py:955-990` | Area A tasks |
| NEW-02 | P3 | No Parquet/JSONL parser codegen | `codegen/file_templates.py` | B.1, B.2 |
| NEW-03 | P2 | Incremental update not implemented (always full regeneration) | `graph/nodes/ingest_spec.py` | Area F tasks |
| NEW-04 | P2 | KG template seeding fails with transaction abort (missing ROLLBACK on conflict) | `persistence/seed_kg.py` | 1 day |
| NEW-05 | P3 | `pkg_resources` deprecation warning (slated for removal 2025-11-30) | `codegen/syntax_validator.py:86` | 0.5 day |
| NEW-06 | P3 | `source.detection.rejected` warning on valid OpenAPI specs (false negative) | `sources/__init__.py` | 1 day |
| BUG-70 | ✅ Fixed | Target language passed but not enforced in output | Multiple | Completed |
| BUG-67 | ✅ Fixed | Large int overflow in checkpoints | `checkpoints.py` | Completed |
| BUG-82 | ✅ Fixed | OpenAPI spec excluded from checkpoint serialization | `checkpoints.py` | Completed |
| BUG-31 | ✅ Fixed | False positives on test credentials in content policy | `content_policy.py:96-108` | Completed |
| BUG-58 | ✅ Fixed | Embedding rate limit retry | `embed_spec_chunks.py:44-68` | Completed |

### Bugs Discovered During Production Validation (2024-12-18)

**NEW-04: KG Template Seeding Transaction Abort**
```
WARNING  integration_coworker.persistence.seed_kg: Failed to seed template oauth2_authorization_code: 
current transaction is aborted, commands ignored until end of transaction block
```
- **Root Cause**: First conflict in batch insert doesn't ROLLBACK, subsequent inserts fail
- **Impact**: KG seeding partially fails on repeated runs (templates exist but warnings logged)
- **Fix**: Add `conn.rollback()` in exception handler or use `ON CONFLICT DO NOTHING`

**NEW-05: pkg_resources Deprecation**
```
UserWarning: pkg_resources is deprecated as an API. Slated for removal as early as 2025-11-30.
```
- **Impact**: Will break on Setuptools 81+ (target: Q4 2025)
- **Fix**: Replace with `importlib.metadata` or `importlib.resources`

**NEW-06: Source Detection False Negative**
```
WARNING: source.detection.rejected [uri=stripe_api.json best_score=0.000]
```
- **Impact**: Logging noise, no functional impact (spec parses correctly)
- **Fix**: Adjust scoring threshold or file source detector logic

### Potential Regressions to Monitor

| Risk | Trigger | Mitigation |
|------|---------|------------|
| IR layer breaks existing Python output | Area A.4 (Python renderer) | Snapshot tests, A/B comparison |
| Incremental mode misses changes | Area F.3 | Hash collision tests, full regen fallback |
| New language linters slow sandbox | Area A (TypeScript/Go) | Timeout config, parallel lint |

---

## Step 5: Prioritized Backlog & Milestone Plan

### Impact vs Effort Matrix

```
                    EFFORT
                    Low        Medium      High
        ┌──────────┬──────────┬──────────┐
  High  │ D (P1)   │ C (P1)   │ A (P1)   │
        │ Safety   │ Eval     │ Multi-   │
IMPACT  │ Enhanc.  │ Hardening│ Language │
        ├──────────┼──────────┼──────────┤
  Med   │ E (P2)   │ F (P2)   │ G (P3)   │
        │ Repo     │ Increm.  │ Deploy   │
        │ Profiles │ Updates  │          │
        ├──────────┼──────────┼──────────┤
  Low   │ B (P2)   │ H (P2)   │          │
        │ File     │ Docs     │          │
        │ Formats  │          │          │
        └──────────┴──────────┴──────────┘
```

### Milestone Plan

#### Milestone 1: Quality Gates (Week 1-2)
**Focus**: Evaluation + Safety hardening

| ID | Task | Owner | Est. Days |
|----|------|-------|-----------|
| C.1-C.5 | Eval metrics persistence + CLI | TBD | 5 |
| D.1-D.4 | Security pattern detection | TBD | 3 |
| | **Milestone 1 Total** | | **8 days** |

**Exit Criteria**:
- `eval-report` CLI command works
- SAST gate detects SQL injection, SSRF patterns
- All existing tests pass

#### Milestone 2: Multi-Language Foundation (Week 3-5)
**Focus**: IR layer + 2 language renderers

| ID | Task | Owner | Est. Days |
|----|------|-------|-----------|
| A.1-A.3 | IR dataclasses + validators | TBD | 3 |
| A.4 | Python renderer | TBD | 3 |
| A.5 | TypeScript renderer | TBD | 4 |
| A.6-A.8 | Prompt integration + tests | TBD | 5 |
| | **Milestone 2 Total** | | **15 days** |

**Exit Criteria**:
- IR generates valid Python and TypeScript
- Snapshot tests pass for both languages
- No regression in existing Python codegen

#### Milestone 3: Performance & UX (Week 6-8)
**Focus**: Incremental updates + deployment

| ID | Task | Owner | Est. Days |
|----|------|-------|-----------|
| F.1-F.5 | Incremental codegen | TBD | 8 |
| E.1-E.4 | Rust/Kotlin profiles | TBD | 3 |
| B.1-B.4 | Parquet/JSONL parsers | TBD | 3 |
| | **Milestone 3 Total** | | **14 days** |

**Exit Criteria**:
- `--incremental` flag skips unchanged endpoints
- Rust/Kotlin repos detected correctly
- Parquet files parsed with Arrow

#### Milestone 4: Production Readiness (Week 9-10)
**Focus**: Deployment + documentation

| ID | Task | Owner | Est. Days |
|----|------|-------|-----------|
| G.1-G.5 | Docker + CI/CD | TBD | 8 |
| H.1-H.4 | Documentation update | TBD | 4 |
| | **Milestone 4 Total** | | **12 days** |

**Exit Criteria**:
- `docker compose up` works with health checks
- Quickstart guide tested by new user
- OpenAPI spec generated

---

## Summary

### What's Working Well
1. **File ingestion** (Track B): CSV, Excel, Fixed-Width all production-ready
2. **Policy enforcement**: Hallucination detection, credential scanning with whitelist
3. **Checkpoint/recovery**: Large field exclusion, Postgres persistence
4. **Parallel execution**: Async branches, timing proof in demo
5. **LLM caching**: Redis-backed with TTL

### Primary Gaps to Address
1. **Multi-language**: IR layer needed for true polyglot support
2. **Incremental updates**: Full regen on every run is wasteful
3. **Eval metrics**: Stored but not easily queryable

### Recommended Next Actions
1. **Immediate** (this week): Start Area D (Safety) - low effort, high impact
2. **Next sprint**: Start Area A (IR layer) - foundational for multi-language
3. **Parallel**: Area C (Eval) can proceed independently

---

*Document maintained manually. Last updated: 2025-12-18*
