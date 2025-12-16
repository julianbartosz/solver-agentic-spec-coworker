# V4 Observability Features Plan

## Overview

This plan enhances observability for two key audiences:
1. **Developers debugging issues** - Deep visibility into agent behavior, LLM calls, and failure modes
2. **Novice users** - Understanding what the coworker does through clear, progressive disclosure

## Current Observability Infrastructure

### What We Have
| Feature | Location | Status |
|---------|----------|--------|
| LangSmith tracing | `llm/client.py`, `runtime.py` | ✅ Full integration |
| Node timing | `state.node_timings` | ✅ For non-LLM nodes |
| LLM fallback tracking | `state.llm_fallbacks` | ✅ Basic list |
| Degraded mode flag | `state.degraded_mode/reason` | ✅ Boolean + reason |
| Error collection | `state.errors` | ✅ List of strings |
| Warnings | `state.warnings` | ✅ V3 addition |
| CLI verbose mode | `--verbose` flag | ✅ Debug logging |
| Markdown report | `build_report` node | ✅ Structured output |
| Trace inspection scripts | `inspect_langsmith_traces.py` | ✅ Manual tooling |

### Current LangSmith Metadata
```python
# Traced with every LLM call:
- task_type: "understand_task" | "plan_flow" | "codegen" | "report"
- model: "gpt-4o-mini" | "claude-3-sonnet" | etc
- provider: "openai" | "anthropic"
- run_id: correlation ID for full run
- provider_code: e.g., "stripe", "mock_payments"
```

---

## Proposed Enhancements

### Phase 1: Enhanced Run Summary (For Novices)

**Goal**: Make the final report tell a clear story of what happened.

#### 1.1 Journey Visualization
Add a visual workflow progress indicator:

```markdown
## Run Journey

🎯 TASK → 📄 INGEST → 🔍 PARSE → 📊 MODEL → 🧠 UNDERSTAND → 📋 PLAN → ⚡ GENERATE → ✅ DONE

Progress: ████████████████████░░░░ 85% (error in validate step)
```

**Implementation**: Add `journey_tracker` field to `WorkflowState`:
```python
# New fields in WorkflowState
journey_steps: List[JourneyStep] = field(default_factory=list)

@dataclass
class JourneyStep:
    node_name: str
    status: Literal["pending", "running", "success", "warning", "error", "skipped"]
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    summary: Optional[str] = None  # One-liner about what happened
```

#### 1.2 "What I Did" Section
Add plain-English explanations to the report:

```markdown
## What the Coworker Did

1. **Read your API spec** (mock_payments_openapi.yaml)
   - Found 5 endpoints, 3 data models (Payment, Customer, Session)
   - Identified this as a REST API using OpenAPI 3.0

2. **Understood your task** ("Create checkout session")
   - Matched to CRUD pattern: CREATE operation
   - Primary entity: CheckoutSession
   - Required endpoint: POST /checkout/sessions

3. **Designed the integration**
   - Created a 4-step workflow: validate → call API → transform → respond
   - Applied 3 policies: retry (3x), timeout (30s), error handling

4. **Generated code**
   - Client class: `MockPaymentsClient` (handles auth, retries)
   - Workflow function: `create_checkout_session()`
   - Unit tests: 3 test cases covering success + error paths
```

**Implementation**: Enhanced `build_report` node that generates human summaries.

#### 1.3 Decision Trail
Show WHY decisions were made:

```markdown
## Key Decisions

| Decision | Reason | Confidence |
|----------|--------|------------|
| Matched to CREATE pattern | Task contains "create", endpoint is POST | 95% |
| Selected `/checkout/sessions` | Best semantic match to "checkout session" | 87% |
| Applied retry policy | Endpoint marked as idempotent | 100% (rule) |
| Used template skeleton | No KG match found, used inference | N/A |
```

**Implementation**: New `state.decision_log: List[Decision]` field populated by nodes.

---

### Phase 2: Developer Debugging Tools

**Goal**: Fast root cause analysis when things go wrong.

#### 2.1 LLM Call Trace Summary
Add structured LLM call tracking:

```python
@dataclass
class LLMCallTrace:
    """Detailed trace of an LLM call for debugging."""
    node_name: str
    call_id: str
    provider: str
    model: str
    task_type: str
    
    # Input/Output
    prompt_preview: str  # First 500 chars
    prompt_tokens: int
    response_preview: str  # First 500 chars
    response_tokens: int
    
    # Timing
    started_at: datetime
    latency_ms: float
    
    # Outcome
    status: Literal["success", "fallback", "error", "retry"]
    fallback_reason: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
```

**CLI Command**: `integration-coworker debug-llm <run_id>`

```
$ integration-coworker debug-llm run_abc123

LLM Call Trace for run_abc123
═══════════════════════════════════════════════════════════

1. understand_task (gpt-4o-mini) - 1,247ms ✓
   Prompt: "Analyze this task: Create checkout session..."
   Response: "{ action: 'create', resource: 'checkout_session'..."
   Tokens: 340 → 89

2. plan_integration_flow (gpt-4o-mini) - 2,103ms ⚠️ FALLBACK
   Prompt: "Design a workflow for: create checkout_session..."
   Response: "Mock response for plan_integration_flow"
   Fallback: Used HTTP method inference (POST → create pattern)

3. generate_code_and_tests (gpt-4) - 8,432ms ✓
   Prompt: "Generate Python code for workflow..."
   Response: "```python\nclass MockPaymentsClient:..."
   Tokens: 2,340 → 1,567
   Refinements: 1 (syntax error fixed)
```

#### 2.2 State Diff Viewer
Show what changed at each node:

**CLI Command**: `integration-coworker debug-state <run_id> [--node <node_name>]`

```
$ integration-coworker debug-state run_abc123 --node build_silver_api_model

State Changes at: build_silver_api_model
══════════════════════════════════════════

ADDED:
  + endpoints: [5 items]
    - POST /checkout/sessions (create_checkout_session)
    - GET /checkout/sessions/{id} (get_checkout_session)
    - ...
  
  + schemas: [3 items]
    - CheckoutSession (6 fields)
    - LineItem (4 fields)
    - PaymentIntent (5 fields)
  
  + entities: [3 items]
    - CheckoutSession
    - LineItem
    - PaymentIntent

UNCHANGED:
  spec_documents: 1
  doc_chunks: 47
```

**Implementation**: Checkpoint after each node with state snapshots.

#### 2.3 Error Context Enrichment
When errors occur, provide actionable context:

```python
@dataclass
class EnrichedError:
    """Error with debugging context."""
    error_type: str  # "llm_timeout", "parse_error", "validation_error", etc.
    message: str
    node_name: str
    
    # Context
    state_snapshot_id: Optional[str] = None
    input_preview: Optional[str] = None  # What triggered the error
    
    # Suggestions
    likely_cause: Optional[str] = None
    suggested_fixes: List[str] = field(default_factory=list)
    
    # Linkage
    langsmith_trace_url: Optional[str] = None
    related_logs: List[str] = field(default_factory=list)
```

Example in report:
```markdown
## Errors

### ❌ Code Generation Failed (generate_code_and_tests)

**Error**: Syntax error in generated code after 3 refinement attempts

**Context**:
- The LLM generated code with an unclosed bracket on line 45
- Refinement attempts: 3 (all failed with same issue)
- Model used: gpt-4o-mini

**Likely Cause**:
- Complex nested structure in the endpoint response schema
- Model may struggle with deeply nested JSON-to-Python mappings

**Suggested Fixes**:
1. Try with a more capable model: `--model gpt-4`
2. Simplify the task to target one endpoint at a time
3. Check if the OpenAPI spec has unusual response structures

**Debug**:
- [View in LangSmith](https://smith.langchain.com/...)
- Run: `integration-coworker debug-llm run_abc123 --node generate_code_and_tests`
```

---

### Phase 3: Real-Time Progress (Interactive Mode)

**Goal**: Live visibility during execution for demos and debugging.

#### 3.1 Progress Streaming
Add a `--progress` flag that streams updates:

```
$ integration-coworker run -s api.yaml -t "Create user" --progress

Integration Co-Worker Starting...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[00:00.0] 🚀 plan_run
          Run ID: run_2024_abc123
          Provider: auto-detected

[00:00.1] 📄 ingest_spec
          Fetched: api.yaml (24 KB)
          Chunks: 47 created

[00:00.3] 🔍 detect_and_parse_spec
          Format: OpenAPI 3.0.1
          Paths: 12

[00:00.8] 📊 build_silver_api_model
          Endpoints: 12
          Schemas: 8
          Entities: 5

[00:02.1] 🧠 understand_task (LLM)
          Model: gpt-4o-mini
          Tokens: 340 → 89
          Result: action=create, resource=user

[00:04.5] 📋 plan_integration_flow (LLM)
          Model: gpt-4o-mini
          Template: inferred from POST pattern
          Nodes: 4

[00:12.3] ⚡ generate_code_and_tests (LLM)
          Model: gpt-4
          Refinements: 1
          Artifacts: 3 files

[00:12.8] ✅ Complete
          Duration: 12.8s
          Status: Success
```

**Implementation**: Event emitter pattern with optional rich console output.

#### 3.2 LangSmith Live Link
Show LangSmith URL early:

```
$ integration-coworker run -s api.yaml -t "Create user"

🔗 Live trace: https://smith.langchain.com/o/org/projects/pr-mundane-creche-14/runs/run_abc123
   (Updates in real-time as nodes execute)
```

---

### Phase 4: Observability Dashboard (Optional UI Enhancement)

**Goal**: Web-based observability for non-CLI users.

#### 4.1 Streamlit Observability Tab
Add to existing `ui/streamlit_app.py`:

```
┌─────────────────────────────────────────────────────────────────┐
│ Integration Co-Worker                                    [🔄]  │
├──────────┬──────────┬──────────────┬────────────┬──────────────┤
│  Inputs  │ Run View │  Artifacts   │    Logs    │ Observability│
├──────────┴──────────┴──────────────┴────────────┴──────────────┤
│                                                                 │
│  📊 Run Timeline                                               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ plan_run ──▶ ingest ──▶ parse ──▶ build ──▶ ...  ✅     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  🧠 LLM Calls (3 total, $0.02 estimated)                       │
│  ┌────────────────────────────────────┬───────────┬─────────┐  │
│  │ Node                              │ Model     │ Latency │  │
│  ├────────────────────────────────────┼───────────┼─────────┤  │
│  │ understand_task                   │ gpt-4o-m  │ 1.2s    │  │
│  │ plan_integration_flow             │ gpt-4o-m  │ 2.1s    │  │
│  │ generate_code_and_tests           │ gpt-4     │ 8.4s    │  │
│  └────────────────────────────────────┴───────────┴─────────┘  │
│                                                                 │
│  📋 Decision Log                                               │
│  • Matched to CREATE pattern (95% confidence)                  │
│  • Selected endpoint: POST /users                              │
│  • Applied 3 policies: retry, timeout, error_handling          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Roadmap

### Phase 1: Enhanced Run Summary (Priority: HIGH)
**Effort**: 2-3 days
**Files to modify**:
- `graph/state.py` - Add `decision_log`, `journey_steps`
- `graph/nodes/build_report.py` - Enhanced report generation
- `graph/runtime.py` - Populate journey tracking

**Deliverables**:
- [ ] Journey visualization in reports
- [ ] "What I Did" plain-English section
- [ ] Decision trail table

### Phase 2: Developer Debugging Tools (Priority: HIGH)
**Effort**: 3-4 days
**Files to modify**:
- `llm/client.py` - Add `LLMCallTrace` collection
- `cli.py` - Add `debug-llm`, `debug-state` commands
- `persistence/checkpoints.py` - State snapshots per node
- `graph/nodes/*.py` - Enriched error context

**Deliverables**:
- [ ] `debug-llm` CLI command
- [ ] `debug-state` CLI command
- [ ] Enriched error messages with suggestions

### Phase 3: Real-Time Progress (Priority: MEDIUM)
**Effort**: 2 days
**Files to modify**:
- `cli.py` - Add `--progress` flag
- `graph/runtime.py` - Event emitter integration
- New: `observability/progress.py` - Progress streaming

**Deliverables**:
- [ ] `--progress` streaming output
- [ ] Early LangSmith link display

### Phase 4: UI Dashboard (Priority: LOW)
**Effort**: 2-3 days
**Files to modify**:
- `ui/streamlit_app.py` - Add Observability tab

**Deliverables**:
- [ ] Timeline visualization
- [ ] LLM call summary table
- [ ] Decision log display

---

## Quick Wins (Can Implement Today)

### 1. Show LangSmith URL in CLI Output
```python
# In cli.py run_integration():
langsmith_enabled = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
if langsmith_enabled:
    project = os.getenv("LANGCHAIN_PROJECT", "default")
    typer.echo(f"🔗 Trace: https://smith.langchain.com/o/default/projects/{project}")
```

### 2. Add Token Usage Summary
```python
# In llm/client.py, track and expose:
state.llm_token_usage = {
    "total_prompt_tokens": 1234,
    "total_completion_tokens": 567,
    "total_cost_usd": 0.02
}
```

### 3. Add Fallback Summary to Report
```python
# In build_report.py:
if state.llm_fallbacks:
    lines.append("## LLM Fallbacks")
    for fb in state.llm_fallbacks:
        lines.append(f"- **{fb['node']}**: {fb['reason']}")
```

---

## Success Metrics

### For Novice Users
- Time to understand what coworker did: < 30 seconds (via report)
- Can answer "why did it do X?" without debugging

### For Developers
- Time to identify error root cause: < 2 minutes
- Can reproduce issues with state snapshots
- Full LLM call history available for any run

---

## Related Files Reference

| File | Purpose |
|------|---------|
| `graph/state.py` | WorkflowState with tracking fields |
| `graph/runtime.py` | Node wrapping, timing, LangSmith setup |
| `llm/client.py` | LLM call tracing, metadata |
| `graph/nodes/build_report.py` | Report generation |
| `cli.py` | CLI commands and output formatting |
| `verify_langsmith.py` | LangSmith connectivity test |
| `inspect_langsmith_traces.py` | Trace inspection utility |

---

## Feature Viability Analysis

### ✅ IMPLEMENT (High Value, Good Fit)

#### 1. Quick Wins (All Three)
**Viability**: ✅ Excellent - minimal code changes, immediate value

| Feature | Effort | Why Implement |
|---------|--------|---------------|
| LangSmith URL in CLI | 5 min | Already have env vars, just echo URL |
| Token usage summary | 30 min | LangChain provides token counts automatically |
| Fallback summary in report | 15 min | `state.llm_fallbacks` already tracked |

#### 2. "What I Did" Section (1.2)
**Viability**: ✅ Excellent - builds on existing report structure

The `build_report.py` already has structured sections. Adding plain-English summaries is straightforward:
- Data already exists in state (endpoints, schemas, entities, workflow_nodes)
- Just need template strings to describe what happened
- No new infrastructure required

#### 3. LangSmith Live Link (3.2)
**Viability**: ✅ Trivial - just display URL earlier in CLI

Currently shows URL in `demo-v1`. Can add to `run` command:
```python
# At start of run_integration():
if langsmith_enabled:
    typer.echo(f"🔗 Trace: https://smith.langchain.com/o/default/projects/{project}/runs/{run_id}")
```

#### 4. Enriched Error Context (2.3)
**Viability**: ✅ Good - infrastructure exists

- Errors are already collected in `state.errors`
- Node name is known when error occurs
- LangSmith trace URL can be constructed from run_id
- Just need to wrap errors with `EnrichedError` dataclass

---

### ⚠️ IMPLEMENT WITH CAUTION (Good Idea, Needs Careful Design)

#### 5. Journey Visualization (1.1)
**Viability**: ⚠️ Moderate - adds complexity to state

**Concerns**:
- Adding `journey_steps: List[JourneyStep]` bloats `WorkflowState`
- Need to update every node to populate journey_steps
- Progress tracking is already in `completed_steps`

**Recommendation**: Instead of new field, DERIVE from existing data:
```python
# In build_report.py, derive journey from:
# - state.completed_steps (what ran)
# - state.errors (what failed)
# - state.node_timings (performance)
```

#### 6. Decision Trail (1.3)
**Viability**: ⚠️ Moderate - requires node modifications

**Concerns**:
- Would need to modify ~10 nodes to log decisions
- Decisions are implicit in LLM responses currently
- Confidence scores would require parsing LLM output

**Recommendation**: Start with rule-based decisions only:
- Pattern matching (95% confidence for explicit rule)
- Template selection (KG match vs inference)
- Policy application (100% for rules)
- Skip LLM confidence parsing initially

#### 7. Progress Streaming (3.1)
**Viability**: ⚠️ Moderate - requires runtime changes

**Concerns**:
- LangGraph doesn't expose per-node callbacks easily
- Would need to wrap each node with event emitter
- `--progress` flag is nice but adds maintenance burden

**Recommendation**: Use existing `--verbose` for now. Streaming is better suited for the Streamlit UI where async updates are natural.

---

### ❌ DON'T IMPLEMENT (Poor Fit or Redundant)

#### 8. State Diff Viewer / `debug-state` CLI (2.2)
**Viability**: ❌ Redundant with LangSmith + checkpoints

**Why Skip**:
- LangSmith already shows inputs/outputs per node
- Checkpoints already save full state JSON to DB
- Building a diff viewer duplicates LangSmith functionality
- You can query `run_checkpoints` table directly if needed

```sql
-- Already possible:
SELECT node_name, state_json 
FROM integration_gold.run_checkpoints 
WHERE run_id = 'run_abc123' 
ORDER BY created_at;
```

#### 9. `debug-llm` CLI Command (2.1)
**Viability**: ❌ Redundant with LangSmith

**Why Skip**:
- LangSmith already provides this exact view
- Prompt/response, tokens, latency all tracked
- Building a local CLI just duplicates LangSmith
- Run `inspect_langsmith_traces.py` for ad-hoc queries

**Exception**: Token cost estimation could be useful addition to report (not a separate command).

#### 10. LLMCallTrace Dataclass (2.1)
**Viability**: ❌ Overengineered

**Why Skip**:
- LangChain already tracks this in LangSmith
- Adding to state bloats memory
- Would need to serialize/deserialize complex objects
- No unique value over LangSmith traces

#### 11. Streamlit Observability Tab (4.1)
**Viability**: ❌ Premature / Low Priority

**Why Skip**:
- UI already exists with basic functionality
- LangSmith provides better visualization
- Adding another observability surface fragments UX
- Better to link to LangSmith from Streamlit

---

### 📋 REVISED IMPLEMENTATION ORDER

Based on this analysis, here's the recommended order:

| Priority | Feature | Effort | Files to Modify | Status |
|----------|---------|--------|-----------------|--------|
| **1** | Quick Win: LangSmith URL in CLI | 5 min | `cli.py` | ✅ Done |
| **2** | Quick Win: Fallback summary in report | 15 min | `build_report.py` | ✅ Done |
| **3** | Quick Win: Token usage summary | 30 min | `llm/client.py`, `state.py`, `build_report.py` | ✅ Done |
| **4** | "What I Did" Section | 1 hr | `build_report.py` | ✅ Done |
| **5** | Enriched Error Context | 2 hr | `domain/models.py`, `build_report.py`, nodes | ✅ Done |
| **6** | Decision Trail (rules only) | 2 hr | `state.py`, nodes, `build_report.py` | ⏸️ Deferred |
| **7** | Journey (derived, not stored) | 1 hr | `build_report.py` | ✅ Done |
| **8** | Warnings Section | 15 min | `build_report.py` | ✅ Done |

**Skip entirely**:
- `debug-llm` / `debug-state` CLI (use LangSmith)
- LLMCallTrace dataclass (redundant)
- Streamlit observability tab (use LangSmith link)
- Progress streaming (use `--verbose` for now)

---

## Implementation Summary (V4 Complete)

### Implemented Features

| Feature | Location | Description |
|---------|----------|-------------|
| **LangSmith URL in CLI** | `cli.py` | Shows trace URL at start of `run` and `demo` commands |
| **Token Usage Tracking** | `llm/client.py`, `state.py` | ContextVar-based aggregation of prompt/completion tokens |
| **LLM Usage in Report** | `build_report.py` | Token counts + estimated costs (gpt-4o-mini and gpt-4 ranges) |
| **"What I Did" Section** | `build_report.py` | `_add_what_i_did_section()` - plain-English explanations |
| **Journey Visualization** | `build_report.py` | `_add_journey_visualization()` - derived from completed_steps/errors |
| **LLM Fallbacks Section** | `build_report.py` | Shows fallback reasons when LLM calls degraded |
| **Enriched Errors** | `build_report.py` | Numbered errors with severity/phase when available |
| **Warnings Section** | `build_report.py` | Non-fatal issues with ⚠️ indicators |

### Key Technical Decisions

1. **Token Tracking via ContextVar**: Used `contextvars.ContextVar` to aggregate tokens across all LLM calls in a run without threading issues.

2. **Derived Journey**: Journey is computed from `completed_steps`, `errors`, and `node_timings` rather than stored separately - reduces state bloat.

3. **LangSmith as Backend**: Didn't duplicate LangSmith functionality - instead surface the URL early so users can access full traces.

4. **Decision Trail Deferred**: Requires modifying all nodes to log decisions - lower ROI compared to LangSmith's trace visibility.

---

## Key Insight: LangSmith is Your Observability Backend

The project already has **excellent observability infrastructure** via LangSmith:
- All LLM calls traced with metadata
- Parent-child relationships preserved
- Token counts, latency, errors tracked
- Inputs/outputs recorded

**The gap is in the FRONTEND** - making this visible without visiting LangSmith:
1. Show LangSmith URL immediately in CLI ✅
2. Summarize key metrics in the report ✅
3. Extract "What I Did" from existing state data ✅

Don't rebuild what LangSmith already does. **Surface it better**.
