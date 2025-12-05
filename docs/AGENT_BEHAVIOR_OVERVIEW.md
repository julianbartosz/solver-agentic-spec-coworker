# Agent Behavior Overview

**Purpose:** High-level overview of the Integration Coworker agent's intended behavior, bounds, failure points, and consistency guarantees.

---

## 1. What is This Agent?

The **Agentic Integration Coworker** is a LangGraph-based workflow that transforms:

```
INPUT:  API Specification(s) + Natural Language Task Description
OUTPUT: Working Python client code + workflow + tests + repo changes + design report
```

### Core Value Proposition

Instead of a developer manually reading API specs and writing integration code, this agent:
1. **Parses** specs (OpenAPI, HTML, PDF)
2. **Normalizes** the API surface into a structured model
3. **Plans** a task-specific workflow based on knowledge graph patterns
4. **Generates** syntactically valid, pattern-compliant code
5. **Wires** the code into a target repository (optional)
6. **Persists** all design decisions for reuse

---

## 2. Behavioral Bounds

### 2.1 What the Agent WILL Do ✅

| Capability | Bound | Notes |
|------------|-------|-------|
| Parse OpenAPI specs | Up to 20MB per spec | Larger specs may timeout |
| Parse HTML documentation | Requires BeautifulSoup4 | Graceful fallback to text heuristics |
| Parse PDFs | Requires pypdf | Graceful fallback to text heuristics |
| Multiple specs per run | Unlimited | Endpoints merged, first spec is "primary" |
| Infer provider from spec | Always attempts | Falls back to "unknown_provider" |
| Generate client code | Always produces something | Template fallback if LLM fails |
| Generate workflow code | Always produces something | Template fallback if LLM fails |
| Generate test code | Always produces something | Template fallback if LLM fails |
| Validate code syntax | Always runs AST check | Invalid code uses template |
| Produce a report | Always | Even on errors |

### 2.2 What the Agent WILL NOT Do ❌

| Non-Capability | Why | Workaround |
|----------------|-----|------------|
| Generate semantically correct code | LLM hallucination risk | Requires human review |
| Handle multi-step workflows | Bug in `_infer_multi_endpoint_workflow` | Single-call tasks only (fix in progress) |
| Execute the generated code | Out of scope for v1 | Manual testing required |
| Write files to disk | Returns `RepoChangeSet` only | User applies changes |
| Handle OAuth flows end-to-end | Complexity | Generates auth structure only |
| Support non-Python runtimes | v1 scope | Python 3.11+ only |

### 2.3 Outer Bounds

```
┌─────────────────────────────────────────────────────────────────┐
│                     OUTER BOUNDS OF BEHAVIOR                     │
├─────────────────────────────────────────────────────────────────┤
│ MAX SPEC SIZE:        20 MB per document                        │
│ MAX SPECS PER RUN:    Unlimited (memory-bound)                  │
│ MAX ENDPOINTS:        ~1000 per spec (performance degrades)     │
│ MAX RUN TIME:         5 minutes (design target)                 │
│ OUTPUT LANGUAGES:     Python only                               │
│ OUTPUT FRAMEWORKS:    httpx client, any workflow structure      │
│ LLM DEPENDENCY:       Required for planning, optional for code  │
│ DB DEPENDENCY:        SQLite (default) or Postgres+pgvector     │
│ EMBEDDING DEPENDENCY: OpenAI API (optional, degrades gracefully)│
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Failure Points & Modes

### 3.1 Critical Failure Points

| Failure Point | Trigger | Result | Recovery |
|---------------|---------|--------|----------|
| **Spec fetch failure** | Network error, 404, timeout | Error logged, run continues with partial data | Retry with local file |
| **Spec parse failure** | Invalid YAML/JSON, unknown format | Falls back to text heuristics | Fix spec or provide OpenAPI |
| **LLM unavailable** | No API key, rate limit, timeout | Uses template-based fallback | Provides minimal but valid code |
| **DAG validation failure** | Multi-step workflow bug | **Raises ValueError, halts workflow** | Use single-endpoint tasks |
| **DB write failure** | Connection lost, constraint violation | Error logged, run continues | Check DB connectivity |

### 3.2 Degraded Mode Triggers

The agent can operate in degraded modes:

```python
# Degraded mode flags in WorkflowState
state.degraded_mode: bool = False
state.degraded_reason: Optional[str] = None
```

| Condition | Degraded Mode | Impact |
|-----------|---------------|--------|
| No OPENAI_API_KEY | Embedding unavailable | No semantic search, KG matching only |
| LLM returns invalid JSON | Parse fallback | Uses heuristic extraction |
| LLM generates invalid code | Template fallback | Valid but generic code |
| No KG templates exist | Inference mode | Uses HTTP method heuristics |
| Empty spec | Minimal Silver model | No endpoints, schemas empty |

### 3.3 Silent Failures (No Crash, But Wrong Output)

⚠️ **These are the most dangerous:**

| Silent Failure | Detection | Mitigation |
|----------------|-----------|------------|
| Wrong endpoint selected for task | Review `endpoint_bindings` | Improve task description specificity |
| Missing request parameters | Review generated code | Check spec has full schemas |
| Wrong workflow order | Review `workflow_nodes` | Use explicit "then/after" language |
| Stale KG template used | Check `template_source` in logs | Clear KG or force inference |

---

## 4. Workflow Execution Flow

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           WORKFLOW EXECUTION FLOW                             │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌─────────┐   ┌─────────────┐   ┌────────────────┐   ┌──────────────────┐   │
│  │plan_run │ → │ ingest_spec │ → │ detect_and_    │ → │ build_silver_    │   │
│  └─────────┘   └─────────────┘   │ parse_spec     │   │ api_model        │   │
│                                   └────────────────┘   └──────────────────┘   │
│                                                               │               │
│                                                               ▼               │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                    SILVER CHECKPOINT (persist)                           │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                   │                                           │
│                                   ▼                                           │
│  ┌───────────────┐   ┌──────────────────┐   ┌────────────────────────────┐   │
│  │understand_task│ → │ align_task_      │ → │ plan_integration_flow      │   │
│  │  (LLM)        │   │ with_kg          │   │  (LLM)                     │   │
│  └───────────────┘   └──────────────────┘   └────────────────────────────┘   │
│                                                               │               │
│                                                               ▼               │
│  ┌──────────────────────────┐   ┌──────────────────────────────────────────┐ │
│  │attach_policies_and_     │ → │ generate_code_and_tests (LLM)            │ │
│  │patterns                  │   └──────────────────────────────────────────┘ │
│  └──────────────────────────┘                     │                           │
│                                                   ▼                           │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                     GOLD CHECKPOINT (persist)                            │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                   │                                           │
│                  ┌────────────────┴────────────────┐                         │
│                  │     plan["use_repo"]?           │                         │
│                  └────────────────┬────────────────┘                         │
│              ┌──── False ─────────┴─────── True ────┐                        │
│              ▼                                      ▼                        │
│  ┌───────────────────────┐           ┌──────────────────────────────────┐   │
│  │validate_integration_  │           │ attach_repo_context →            │   │
│  │design                 │◄──────────│ analyze_repo_layout →            │   │
│  └───────────────────────┘           │ apply_repo_integration_changes   │   │
│              │                       └──────────────────────────────────┘   │
│              ▼                                                               │
│       ┌──────────────┐                                                       │
│       │ has_errors?  │                                                       │
│       └──────┬───────┘                                                       │
│          Yes │ No                                                            │
│              ▼ ▼                                                             │
│  ┌─────────────────┐   ┌──────────────────┐   ┌─────────────────────────┐   │
│  │ handle_error    │ → │ build_report     │ → │ persist_run_outcome     │   │
│  │ (sets flag)     │   │ (LLM summary)    │   │ (final checkpoint)      │   │
│  └─────────────────┘   └──────────────────┘   └─────────────────────────┘   │
│                                                               │               │
│                                                               ▼               │
│                                                           [END]               │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Consistency Guarantees

### 5.1 Guaranteed Invariants ✅

| Invariant | Enforcement Mechanism |
|-----------|----------------------|
| **Run always produces a report** | `build_report` runs even after `handle_error` |
| **Dry-run never writes to DB** | All persistence nodes check `options.dry_run` |
| **Generated code is syntactically valid** | AST validation + template fallback |
| **Same input → same output (deterministic)** | LLM temperature=0, seeded run_id |
| **Checkpoints saved after each node** | `timed_node()` wrapper |
| **Errors don't crash the workflow** | Try/catch + state.errors accumulation |

### 5.2 Consistency Focus Areas

These areas have the highest risk of inconsistency:

| Area | Risk | Current Mitigation | Recommendation |
|------|------|-------------------|----------------|
| **LLM output parsing** | 🔴 High | Regex fallback + refinement loop | Add JSON schema validation |
| **Endpoint selection** | 🟠 Medium | Hybrid graph+embedding scoring | Improve task→endpoint matching |
| **Workflow ordering** | 🟠 Medium | Dependency-based edges | Fix multi-step bug |
| **Code correctness** | 🟠 Medium | AST validation only | Add unit test execution |
| **KG template staleness** | 🟡 Low | Score by recency | Add TTL to templates |

### 5.3 Idempotency

```python
# Running the same inputs twice produces the same outputs
result1 = design_and_generate_integration(spec_refs, task_description)
result2 = design_and_generate_integration(spec_refs, task_description)

assert result1.code_artifacts == result2.code_artifacts  # ✅ True
assert result1.workflow_nodes == result2.workflow_nodes  # ✅ True
```

**Exception:** If KG is updated between runs, template selection may differ.

---

## 6. Where to Focus for Production Reliability

### 6.1 Priority 1: Input Validation

```
CURRENT:  Specs are trusted, minimal validation
NEEDED:   OpenAPI schema validation before parsing
WHY:      Bad specs cause cascading failures throughout workflow
```

### 6.2 Priority 2: Multi-Step Workflow Fix

```
CURRENT:  _infer_multi_endpoint_workflow generates invalid DAG
NEEDED:   Add start/end nodes, normalize types
WHY:      Blocks real-world tasks like "create X then notify Y"
```

### 6.3 Priority 3: Output Verification

```
CURRENT:  AST validation only (syntax)
NEEDED:   Execute generated tests in sandbox
WHY:      Semantic correctness can't be validated without execution
```

### 6.4 Priority 4: Recovery & Resume

```
CURRENT:  Checkpoints saved but no resume API
NEEDED:   run_from_checkpoint(run_id, node_name) function
WHY:      Long runs shouldn't restart from scratch on failure
```

---

## 7. Observability

### 7.1 Tracing

```bash
# Enable LangSmith tracing
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_PROJECT=integration-coworker
```

All LLM calls are traced. Non-LLM nodes include:
- `node_type` tag (pure-python, db-write, api-call, llm)
- `responsibility` description
- `duration_ms` timing

### 7.2 Logging

```python
import logging
logging.getLogger("integration_coworker").setLevel(logging.DEBUG)

# Key loggers:
# - integration_coworker.graph.nodes.*  (node execution)
# - integration_coworker.llm            (LLM calls)
# - integration_coworker.persistence    (DB writes)
```

### 7.3 State Inspection

```python
# After any run, inspect the full state
result = design_and_generate_integration(...)

# What was parsed
result.endpoints        # Silver: API surface
result.schemas          # Silver: Data models
result.entities         # Silver: Business objects

# What was planned
result.workflow_nodes   # Gold: Workflow structure
result.workflow_edges   # Gold: Workflow connections
result.endpoint_bindings  # Gold: Node → Endpoint mappings

# What was generated
result.code_artifacts   # Generated code
result.errors           # Any errors encountered
result.completed_steps  # Which nodes ran
```

---

## 8. Quick Reference: What Can Go Wrong

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| "Flow must have exactly one start node" | Multi-step task triggered broken code path | Use single-endpoint task or fix P0 bug |
| Empty `code_artifacts` | LLM failed + template fallback also failed | Check LLM API key, review logs |
| Wrong endpoint in binding | Task description too vague | Be more specific: "POST /users to create user" |
| Missing policies in result | Known gap (P1 fix) | Access via internal state for now |
| "Embedding client unavailable" | No OPENAI_API_KEY | Set API key or accept degraded mode |
| "No template found" | Empty KG (first run) | Normal - uses inference fallback |
| Very slow run (>5 min) | Large spec or many chunks | Reduce spec size, enable streaming |

---

## 9. Summary

### The Agent's Job:
> Transform API specs + task descriptions into production-ready integration code with consistent patterns.

### Guarantees:
- Always produces a report
- Generated code is syntactically valid
- Dry-run mode never writes to database
- Failures degrade gracefully, don't crash

### Current Gaps:
- Multi-step workflows broken (P0)
- Policies not exposed in result (P1)
- No semantic code correctness validation

### Best Practices:
1. Start with single-endpoint tasks
2. Be specific in task descriptions
3. Review generated code before using
4. Enable LangSmith tracing for debugging
5. Use `dry_run=True` for testing
