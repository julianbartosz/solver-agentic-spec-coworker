# ARCHITECTURE.md Rewrite Plan

> **Date**: December 10, 2025  
> **Purpose**: Blueprint for rewriting ARCHITECTURE.md based on P0+P1 audit findings  
> **Status**: Ready for execution

---

## A. Proposed Section Outline for New ARCHITECTURE.md

### 1. High-Level Overview
- **Purpose**: One-paragraph summary of what the system does
- **Keep**: Core value proposition paragraph
- **Add**: Version/status badge, link to detailed docs

### 2. Quick Start Reference
- **Purpose**: Minimal info to get started
- **Keep**: Entry point signature, basic example
- **Add**: Minimum viable environment table
- **Move**: Current "Quick Reference" content here

### 3. System Architecture

#### 3.1 Entry Points
- **Purpose**: Document CLI and Python API
- **Keep**: Entry point table
- **Add**: Full CLI command list, `IntegrationOptions` fields, `IntegrationResult` fields

#### 3.2 Workflow Engine
- **Purpose**: LangGraph execution model
- **Keep**: General description of node-based execution
- **Fix**: Node count (21, not 22)
- **Add**: 
  - Conditional edges (repo + error routing)
  - Checkpoint recovery mechanism (dual-layer)
  - Parallel execution (`PARALLEL_WORKFLOW`)
  - `timed_node()` decorator behavior
  - `WORKFLOW_NODE_ORDER` constant

#### 3.3 Node Catalog
- **Purpose**: Complete list of all nodes with phase grouping
- **Fix**: Match actual node files (21 nodes)
- **Add**: Phase grouping (Ingestion, Silver, Gold, Generation, Repo, Finalization)
- **Add**: Per-node field read/write contracts

### 4. Data Model

#### 4.1 WorkflowState Structure
- **Purpose**: Document all state fields
- **Fix**: Bronze layer includes 4 fields (not just `openapi_spec`)
- **Add**: Complete field list with types and layer assignments

#### 4.2 Bronze-Silver-Gold Model
- **Keep**: Medallion model explanation
- **Fix**: Bronze layer definition (spec_documents, spec_sections, doc_chunks, openapi_spec)
- **Add**: Checkpoint invariants at each boundary

#### 4.3 Domain Models
- **Purpose**: Document Silver and Gold dataclasses
- **Keep**: General structure
- **Add**: Complete model list from `domain/models.py`
- **Add**: Relationship diagram

### 5. Database Schema

#### 5.1 Tables by Schema
- **Purpose**: Complete table inventory
- **Fix**: Add all ~27 tables (currently missing Bronze, repo_meta, kg, feedback tables)
- **Add**: Schema grouping (spec_bronze, spec_silver, integration_gold, repo_meta, kg)

#### 5.2 Postgres vs SQLite
- **Keep**: Basic comparison
- **Add**: Feature differences (pgvector, schemas, pooling)
- **Add**: SQLite deprecation warning

#### 5.3 Connection Management
- **Purpose**: Document ConnectionWrapper and pooling
- **Add**: `get_connection()` contract, cleanup behavior

### 6. LLM Integration

#### 6.1 Archetype System
- **Purpose**: Document YAML-based LLM configuration
- **Add**: Archetype YAML schema (model, prompting, retrieval, parallelism)
- **Add**: Flow from YAML → client → API call

#### 6.2 Provider Support
- **Purpose**: Document multi-provider architecture
- **Keep**: Provider table
- **Fix**: Add Google Gemini support
- **Add**: Fallback chain behavior

#### 6.3 LLM Modes
- **Purpose**: Document REAL/MOCK/RECORD/REPLAY
- **Add**: Complete section (currently missing)
- **Add**: `LLM_MODE` env var, legacy `USE_MOCK_LLM`

#### 6.4 Caching
- **Purpose**: Document Redis cache behavior
- **Add**: Cache key format, TTL, graceful degradation

#### 6.5 Retry Logic
- **Purpose**: Document retry behavior
- **Add**: `with_retry()` behavior, retryable vs terminal errors

#### 6.6 Deprecations
- **Purpose**: Document deprecated APIs
- **Add**: `call_llm()` deprecation, `models.yaml` status

### 7. Security & Safety

#### 7.1 Input Sanitization
- **Purpose**: Document sanitizer.py behavior
- **Add**: Pattern list, sanitization levels, detection function

#### 7.2 Prompt Hardening
- **Purpose**: Document safety.py
- **Add**: Safety preamble content, `harden_system_prompt()` behavior

#### 7.3 Code Security Validation
- **Keep**: AST validation mention
- **Add**: `SecurityVisitor` details, forbidden functions/calls

#### 7.4 Content Policy
- **Purpose**: Document content_policy.py
- **Add**: Violation types, severity levels, endpoint validation

#### 7.5 Secrets Handling
- **Purpose**: Document API key handling
- **Add**: Storage policy, never-log policy

### 8. Observability

#### 8.1 LangSmith Tracing
- **Purpose**: Document tracing configuration
- **Add**: Env vars, automatic tracing behavior

#### 8.2 Trace Metadata
- **Purpose**: Document what's attached to traces
- **Add**: Metadata fields, tags, NODE_METADATA catalog

#### 8.3 Token Usage Tracking
- **Purpose**: Document token counting
- **Add**: `init_token_usage()`, `_track_token_usage()`, report integration

#### 8.4 Run Inspection
- **Purpose**: Document post-run inspection tools
- **Add**: `inspect_node_details.py`, `inspect_langsmith_traces.py`

### 9. Configuration

#### 9.1 Environment Variables
- **Purpose**: Complete env var reference
- **Add**: Full table of required/optional vars with effects

#### 9.2 Settings Hierarchy
- **Purpose**: Document precedence order
- **Add**: env var → archetype → models.yaml → defaults

#### 9.3 Feature Flags
- **Purpose**: Document all flags
- **Add**: PARALLEL_WORKFLOW, STREAMING_PERSISTENCE, LLM_CACHE_ENABLED, etc.

### 10. Performance & Reliability

#### 10.1 Streaming Persistence
- **Purpose**: Document memory optimization
- **Add**: Thresholds, batch sizes, auto-enable behavior

#### 10.2 Parallel Execution
- **Purpose**: Document parallel graph
- **Add**: Fan-out/fan-in pattern, sync node, timeout

#### 10.3 Degraded Mode
- **Purpose**: Document fallback behavior
- **Fix**: Clarify that `degraded_mode` is observational only
- **Add**: What actually changes behavior (template fallback)

#### 10.4 Template Fallbacks
- **Keep**: AST validation + template fallback
- **Add**: Fallback chain details, language awareness

### 11. Recovery & Resume

#### 11.1 Checkpoint Mechanism
- **Purpose**: Document dual-layer checkpointing
- **Add**: LangGraph native + application-level
- **Add**: Skip detection via `completed_steps`

#### 11.2 Auto-Resume
- **Purpose**: Document CLI resume features
- **Add**: `--resume`, `--auto-resume` behavior

### 12. Repository Integration

#### 12.1 Repo Profile System
- **Keep**: Two-layer architecture description
- **Fix**: Confidence thresholds (0.8, 0.4, 0.3 - not 0.65)
- **Add**: Source labels

#### 12.2 Known Archetypes
- **Keep**: Archetype table
- **Add**: Detection patterns

### 13. Knowledge Graph

#### 13.1 GraphRAG Approach
- **Keep**: Hybrid retrieval description
- **Add**: Actual kg/ implementation details

#### 13.2 Pattern Learning
- **Purpose**: Document persist_kg_learning
- **Add**: What gets learned, stored patterns

### 14. Testing

#### 14.1 Testing Strategy
- **Purpose**: Document test organization
- **Add**: Unit/integration/E2E breakdown

#### 14.2 Mock Modes
- **Purpose**: Document testing with mocks
- **Add**: LLM_MODE=mock, test fixtures

### 15. Appendix

#### 15.1 Node Phase Diagram
- **Purpose**: Visual execution flow
- **Add**: ASCII or Mermaid diagram

#### 15.2 Complete CLI Reference
- **Purpose**: All commands and options
- **Add**: Full command documentation

#### 15.3 Complete Field Reference
- **Purpose**: All WorkflowState fields
- **Add**: Type, default, layer, producer, consumers

---

## B. Per-Section Change Notes

### Current Section: High-level Overview
**Keep**: Core value proposition, input/output description  
**Remove**: Nothing  
**Correct**: Nothing  
**Add**: Version badge, quick links to detailed sections

---

### Current Section: Data Flow
**Keep**: General flow description, phase grouping  
**Correct**:
- Node count: "22 LangGraph nodes" → "21 LangGraph nodes"
- Add `persist_kg_learning` to the flow description (currently mentioned but placement unclear)
**Add**:
- Conditional edges: error routing (`check_for_errors_after_validation`)
- Parallel execution note (when `PARALLEL_WORKFLOW=true`)

---

### Current Section: Silver–Gold Medallion Model
**Keep**: Layer descriptions, table lists  
**Correct**:
- Bronze layer: Currently just "Raw inputs | Spec files, SHA256 hashes" → expand to include all 4 Bronze fields
**Add**:
- Complete table list (~27 tables, not just Silver/Gold)
- Bronze, repo_meta, kg schema tables

---

### Current Section: Main Components and Responsibilities
**Keep**: Entry points table, state management table  
**Correct**: Nothing (accurate)  
**Add**:
- Per-node phase grouping
- Public vs internal API distinction

---

### Current Section: External Dependencies
**Keep**: Dependency table  
**Correct**:
- Add Google Gemini as supported provider
- Clarify Anthropic is optional (fallback exists)
**Add**:
- Redis for caching
- Provider fallback chain documentation

---

### Current Section: Agent Behavior Bounds
**Keep**: Most content is accurate  
**Correct**:
- "Idempotent Operations" claim needs qualification (LLM variability)
**Add**:
- Actual timeout values where they exist
- Feature flag effects on bounds

---

### Current Section: Repo Profile System
**Keep**: Two-layer architecture, archetypes table  
**Correct**:
- Confidence threshold: 0.65 applies to codegen path fixer, NOT repo profiles
- Repo profile thresholds: HIGH=0.8, LOW=0.4, VERY_LOW=0.3
**Add**:
- Source label meanings

---

### Current Section: Hybrid Retrieval (KG + Semantic Search)
**Keep**: General approach  
**Correct**: Nothing (accurate description)  
**Add**:
- What `persist_kg_learning` actually stores
- Cross-reference to kg/ module

---

### Current Section: Quick Reference
**Keep**: Entry point example  
**Correct**: Nothing  
**Add**:
- Full `IntegrationOptions` field table
- Full `IntegrationResult` field table
- Minimum environment setup

---

### Missing Sections to Add

1. **LLM Configuration** (new section)
   - Archetype YAML schema
   - Provider selection
   - Fallback chain
   - Cache behavior
   - Retry logic
   - LLM modes

2. **Security & Safety** (new section)
   - Input sanitization
   - Prompt hardening
   - Code validation
   - Content policy
   - Secrets handling

3. **Observability** (new section)
   - LangSmith configuration
   - Trace metadata
   - Token tracking
   - Run inspection

4. **Configuration Reference** (new section)
   - Complete env var table
   - Feature flags
   - Settings hierarchy

5. **Recovery & Resume** (new section)
   - Checkpoint mechanism
   - Auto-resume behavior

6. **Testing** (new section)
   - Test organization
   - Mock modes

---

## C. Doc-Drift Fix List

| # | Area/View | Current Claim | True Behavior | Fix Location |
|---|-----------|---------------|---------------|--------------|
| 1 | Runtime | "22 LangGraph nodes: 21 in main path plus one error node" | 21 nodes registered (including error node) | §3.2 Workflow Engine |
| 2 | Runtime | Conditional routing mentions only repo | Two conditionals: repo + error routing | §3.2 Workflow Engine |
| 3 | Runtime | No checkpoint recovery documentation | Dual-layer: LangGraph native + application-level | §11.1 Checkpoint Mechanism |
| 4 | Runtime | No parallel execution documentation | Full `build_parallel_graph()` exists | §10.2 Parallel Execution |
| 5 | Data | "Bronze → openapi_spec (parsed dict)" only | Bronze includes spec_documents, spec_sections, doc_chunks, openapi_spec | §4.1 WorkflowState Structure |
| 6 | Persistence | Only Silver/Gold tables listed | ~27 tables across 5 schemas | §5.1 Tables by Schema |
| 7 | Persistence | No ConnectionWrapper documentation | ConnectionWrapper manages lifecycle | §5.3 Connection Management |
| 8 | LLM | No archetype→LLM call flow | Full flow exists from YAML to API | §6.1 Archetype System |
| 9 | LLM | No provider fallback chain | openai→anthropic→google→mock | §6.2 Provider Support |
| 10 | LLM | No Google Gemini mention | Gemini 2.5 Flash/Pro supported | §6.2 Provider Support |
| 11 | LLM | No `call_llm()` deprecation | Deprecated, use `call_llm_for_node()` | §6.6 Deprecations |
| 12 | LLM | No archetype YAML schema | Full schema exists | §6.1 Archetype System |
| 13 | LLM | No LLM modes documentation | REAL/MOCK/RECORD/REPLAY | §6.3 LLM Modes |
| 14 | LLM | No cache documentation | Redis cache with key structure | §6.4 Caching |
| 15 | LLM | No retry documentation | with_retry() with exponential backoff | §6.5 Retry Logic |
| 16 | CLI | Partial command list | 12+ commands | §15.2 CLI Reference |
| 17 | CLI | IntegrationOptions incomplete | 10 fields documented in P0_REST | §3.1 Entry Points |
| 18 | CLI | IntegrationResult incomplete | ~20 fields documented in P0_REST | §3.1 Entry Points |
| 19 | Config | No complete env var reference | Full list in P0_REST | §9.1 Environment Variables |
| 20 | Config | No precedence order | env→archetype→models.yaml→defaults | §9.2 Settings Hierarchy |
| 21 | Config | No Settings dataclass docs | Structure in P0_REST | §9.2 Settings Hierarchy |
| 22 | Config | No feature flags table | Multiple flags exist | §9.3 Feature Flags |
| 23 | Quality | "Idempotent Operations" unqualified | True for persistence, not for LLM outputs | §10.3 Degraded Mode |
| 24 | Quality | `degraded_mode` implies behavior change | Observational only, no behavior change | §10.3 Degraded Mode |
| 25 | Repo | Confidence threshold 0.65 | Applies to codegen, not repo (which uses 0.8/0.4/0.3) | §12.1 Repo Profile System |
| 26 | Security | No sanitization docs | sanitizer.py patterns and levels | §7.1 Input Sanitization |
| 27 | Security | No content policy docs | content_policy.py enforcement | §7.4 Content Policy |
| 28 | Observability | No LangSmith config docs | Env vars and metadata | §8.1 LangSmith Tracing |
| 29 | Observability | No token tracking docs | init_token_usage(), _track_token_usage() | §8.3 Token Usage Tracking |
| 30 | Recovery | No resume mechanism docs | CLI auto-resume, checkpoint loading | §11.2 Auto-Resume |
| 31 | Testing | No testing strategy | ~70 test files organized by type | §14 Testing |
| 32 | Streaming | No streaming persistence docs | Thresholds, batch sizes, auto-enable | §10.1 Streaming Persistence |

---

## D. Execution Priority

### Phase 1: Critical Fixes (Must fix before any use)
1. **Node count**: 21, not 22
2. **Conditional edges**: Add error routing
3. **Checkpoint recovery**: Add section explaining mechanism
4. **Parallel execution**: Add section if `PARALLEL_WORKFLOW` is production-ready
5. **Env var reference**: Users need this to run the system
6. **Degraded mode**: Clarify observational-only nature

### Phase 2: Completeness Fixes (Important for maintainers)
7. **Archetype system**: Full documentation of YAML schema
8. **LLM modes**: REAL/MOCK/RECORD/REPLAY
9. **Provider fallback**: Document chain
10. **Cache behavior**: Key structure, TTL
11. **Retry logic**: Retryable vs terminal
12. **Sanitization**: Pattern list, levels
13. **Token tracking**: How it works

### Phase 3: Polish (Nice to have)
14. **Complete CLI reference**: All commands
15. **IntegrationOptions/Result**: All fields
16. **Testing strategy**: Organization
17. **Security deep-dive**: Content policy, secrets
18. **Streaming persistence**: Thresholds and behavior

---

## E. Rewrite Checklist

- [ ] Update node count from 22 to 21
- [ ] Add error conditional edge documentation
- [ ] Add checkpoint recovery section
- [ ] Add parallel execution section
- [ ] Expand Bronze layer definition
- [ ] Add complete table inventory
- [ ] Add archetype YAML schema
- [ ] Add provider fallback chain
- [ ] Add Google Gemini to providers
- [ ] Add LLM modes section
- [ ] Add cache behavior section
- [ ] Add retry logic section
- [ ] Add call_llm() deprecation note
- [ ] Add complete env var table
- [ ] Add feature flags table
- [ ] Qualify idempotency claim
- [ ] Clarify degraded_mode is observational
- [ ] Fix repo confidence thresholds
- [ ] Add sanitization section
- [ ] Add prompt hardening section
- [ ] Add content policy section
- [ ] Add LangSmith tracing section
- [ ] Add token tracking section
- [ ] Add auto-resume section
- [ ] Add testing strategy section
- [ ] Add streaming persistence section
- [ ] Add complete CLI reference
- [ ] Add IntegrationOptions table
- [ ] Add IntegrationResult table
- [ ] Add node phase diagram
- [ ] Add field read/write contracts

---

## F. Source References

All changes should cite the audit documents:
- `ARCHITECTURE_AUDIT_RUNTIME_DATA.md` — Runtime/Workflow and Data/State P0
- `ARCHITECTURE_AUDIT_P0_REST.md` — Remaining P0 questions
- `ARCHITECTURE_AUDIT_P1.md` — All P1 questions

Evidence files:
- `graph/runtime.py` — Workflow engine, node wiring
- `graph/state.py` — WorkflowState definition
- `domain/models.py` — Silver/Gold dataclasses
- `persistence/db.py` — Database abstraction
- `llm/client.py` — LLM client implementation
- `llm/cache.py` — Cache implementation
- `llm/sanitizer.py` — Sanitization
- `llm/safety.py` — Prompt hardening
- `llm/content_policy.py` — Content validation
- `config/__init__.py` — Settings and archetypes
- `config/llm_mode.py` — LLM modes
- `cli.py` — CLI commands
- `api/entrypoint.py` — Python API
- `api/types.py` — IntegrationOptions, IntegrationResult
