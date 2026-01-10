# Implementation Spec: Production Output, Progress & Export Systems

**Status:** APPROVED - Ready for Implementation  
**Date:** 2025-01-09  
**Author:** Production Readiness Team  
**Supersedes:** ADR-PRODUCTION-OUTPUT-PROGRESS.md (Proposed, 2025-01-20)

---

## 0. Reality Check Summary (Evidence-Based Current State)

### 0.1 What Exists Today

| Component | File:Lines | Evidence |
|-----------|-----------|----------|
| `_log_graph_event()` | [runtime.py:757-810](../src/integration_coworker/graph/runtime.py#L757) | Emits `graph.node.{start,end,skip,error,timeout}` to logger + GRAPH_TRACE.jsonl |
| `log_step_event()` | [node_trace.py:210-270](../src/integration_coworker/graph/node_trace.py#L210) | Sub-step tracing with W3C trace IDs |
| `_write_graph_trace_line()` | [runtime.py:815-835](../src/integration_coworker/graph/runtime.py#L815) | Appends JSON to `GRAPH_TRACE.jsonl` |
| `step_context()` | [node_trace.py:280-330](../src/integration_coworker/graph/node_trace.py#L280) | Context manager for step timing |
| CLI `--json` flag | [cli.py:317](../src/integration_coworker/cli.py#L317) | Outputs `json.dumps({"run_id":..., "report_markdown":...})` |
| `IntegrationResult` | [api/types.py:85-180](../src/integration_coworker/api/types.py#L85) | 20+ fields including `code_artifacts: List[CodeArtifact]` |
| `CodeArtifact` | [domain/models.py:1-50](../src/integration_coworker/domain/models.py) | Has `rel_path`, `content`, `language`, `artifact_type` |
| Health server | [health/server.py:1-200](../src/integration_coworker/health/server.py) | stdlib http.server (NOT FastAPI) |
| Settings | [config/__init__.py:1-600](../src/integration_coworker/config/__init__.py) | `@dataclass Settings` with env var defaults |

### 0.2 What Is Missing (With Grep Evidence)

```bash
# NO streaming infrastructure:
$ grep -rE "SSE|WebSocket|EventSource|astream_events" src/ → 0 matches

# NO output format selection:
$ grep -rE "OutputFormat|--format" src/integration_coworker/cli.py → 0 matches

# NO export endpoint:
$ grep -rE "/export|export_zip|export_code" src/ → 0 matches

# NO rate limiting middleware:
$ grep -rE "slowapi|ratelimit|Limiter" src/ → 0 matches

# NO RFC 7807 errors:
$ grep -rE "problem\+json|ProblemDetail" src/ → 0 matches
```

### 0.3 Architecture Context

- **NO FastAPI app**: The project uses Typer CLI + stdlib http.server for health checks
- **Event emission exists**: `_log_graph_event()` writes to JSONL but has no callback mechanism
- **Progress % calculable**: `WORKFLOW_NODE_ORDER` in [node_names.py](../src/integration_coworker/graph/node_names.py) has ordered node list
- **Config pattern**: Dataclass-based `Settings` with env var defaults, `get_settings()` accessor

---

## 1. Goals and Non-Goals

### 1.1 Goals

1. **G1**: Enable real-time progress visibility during workflow execution (CLI + future API)
2. **G2**: Make output format configurable (`--format json|yaml|markdown|html`)
3. **G3**: Allow code artifact download without repo integration
4. **G4**: Standardize error responses across CLI output
5. **G5**: Add audit logging for security/debugging

### 1.2 Non-Goals (Explicitly Out of Scope)

- **NG1**: Build a FastAPI REST server (this is a CLI-first tool)
- **NG2**: WebSocket support (SSE is simpler, no existing WS infra)
- **NG3**: Webhook delivery (requires persistence for retry/DLQ - deferred)
- **NG4**: Rate limiting (no public API server to rate-limit)
- **NG5**: Bulk operations (single-run CLI is the contract)

---

## 2. Public Contracts

### 2.1 Progress Event Schema (New Module)

```python
# src/integration_coworker/progress/events.py
from dataclasses import dataclass
from typing import Optional, Dict, Any, Literal

ProgressEventType = Literal[
    "workflow.start", "workflow.end", "workflow.error",
    "node.start", "node.end", "node.skip", "node.error"
]

@dataclass(frozen=True)
class ProgressEvent:
    """Immutable progress event for streaming to subscribers."""
    run_id: str
    event_type: ProgressEventType
    timestamp: str  # ISO 8601 UTC
    node_name: Optional[str] = None
    node_index: Optional[int] = None  # 0-based position in workflow
    total_nodes: int = 0
    progress_pct: float = 0.0  # 0.0-1.0
    duration_ms: Optional[float] = None
    message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
```

### 2.2 Progress Callback Protocol

```python
# src/integration_coworker/progress/emitter.py
from typing import Protocol, Callable

class ProgressCallback(Protocol):
    """Protocol for progress event subscribers."""
    def __call__(self, event: ProgressEvent) -> None: ...

# Type alias for callback functions
ProgressCallbackFn = Callable[[ProgressEvent], None]
```

### 2.3 Output Format Enum (New Module)

```python
# src/integration_coworker/output/formats.py
from enum import Enum

class OutputFormat(str, Enum):
    """Supported output formats for CLI/API."""
    MARKDOWN = "markdown"
    JSON = "json"
    YAML = "yaml"
    HTML = "html"
```

### 2.4 CLI Flag Changes

```python
# cli.py run_integration() - new parameters
format: OutputFormat = typer.Option(
    OutputFormat.MARKDOWN,
    "--format", "-F",
    help="Output format: markdown, json, yaml, html"
)
# --json remains as alias for --format json (backward compat)
```

### 2.5 Export Archive Contract

```python
# src/integration_coworker/output/export.py
@dataclass
class ExportManifest:
    """Manifest included in exported archives."""
    run_id: str
    export_timestamp: str
    task_description: str
    provider_code: Optional[str]
    artifact_count: int
    artifacts: List[Dict[str, Any]]  # {id, type, language, rel_path, size_bytes}
```

---

## 3. File-by-File Change List

### 3.1 New Files

| File | Purpose |
|------|---------|
| `src/integration_coworker/progress/__init__.py` | Package init, exports `ProgressEmitter`, `ProgressEvent` |
| `src/integration_coworker/progress/events.py` | `ProgressEvent` dataclass, `ProgressEventType` |
| `src/integration_coworker/progress/emitter.py` | `ProgressEmitter` class with thread-safe callback registry |
| `src/integration_coworker/progress/callbacks.py` | Built-in callbacks: `RichProgressCallback`, `FileCallback` |
| `src/integration_coworker/output/__init__.py` | Package init |
| `src/integration_coworker/output/formats.py` | `OutputFormat` enum, `FormatOptions` dataclass |
| `src/integration_coworker/output/renderer.py` | `render_result()` dispatcher, format-specific renderers |
| `src/integration_coworker/output/export.py` | `CodeExporter` class with zip/tar.gz generation |
| `tests/progress/test_emitter.py` | Unit tests for emitter |
| `tests/progress/test_callbacks.py` | Unit tests for callbacks |
| `tests/output/test_formats.py` | Unit tests for format rendering |
| `tests/output/test_export.py` | Unit tests for export (zip-slip, path traversal) |

### 3.2 Modified Files

| File | Changes |
|------|---------|
| `src/integration_coworker/graph/runtime.py` | Add `_emit_to_progress_subscribers()` call in `_log_graph_event()` |
| `src/integration_coworker/cli.py` | Add `--format` flag, update `_handle_run_result()` to use renderers |
| `src/integration_coworker/config/__init__.py` | Add `ProgressConfig` dataclass with `PROGRESS_*` env vars |

---

## 4. Detailed Design Decisions

### 4.1 Progress Streaming: SSE vs WebSocket vs Polling

**Options Considered:**

| Option | Pros | Cons |
|--------|------|------|
| **SSE (Server-Sent Events)** | Unidirectional (fits our use case), HTTP-based, simple | Requires HTTP server we don't have |
| **WebSocket** | Bidirectional, real-time | Overkill, no existing infra |
| **Callback-based** | No server needed, works in CLI | Requires in-process consumption |
| **File-based (JSONL)** | Already exists | Requires polling, not real-time |

**Decision: Callback-based with optional Rich progress bar**

**Rationale:**
1. No existing HTTP server to add SSE to (health server is internal-only)
2. CLI is the primary interface - callback enables Rich progress bar
3. JSONL trace already exists for post-hoc analysis
4. Future SSE can wrap callbacks when/if FastAPI is added

### 4.2 Output Format: Structured JSON vs Wrapped Markdown

**Options Considered:**

| Option | Pros | Cons |
|--------|------|------|
| **Structured JSON** | Machine-parseable, includes all fields | Breaking change to `--json` |
| **Keep current** | Backward compat | Useless for automation |

**Decision: Structured JSON with deprecation warning**

**Rationale:**
- Current `--json` outputs `{"report_markdown": "..."}` which is useless
- New behavior: full `IntegrationResult` serialized
- Add deprecation warning in v1.x, remove in v2.0

### 4.3 Export Security: Zip-Slip Prevention

**Threat Model:**
- `CodeArtifact.rel_path` comes from LLM generation
- Malicious paths like `../../../etc/passwd` could escape archive

**Mitigation:**
```python
def _sanitize_path(rel_path: str) -> str:
    """Prevent path traversal attacks (zip-slip)."""
    # Normalize and reject absolute paths
    normalized = os.path.normpath(rel_path)
    if normalized.startswith(('/', '..', '\\')):
        raise ValueError(f"Invalid path: {rel_path}")
    # Reject path components that escape
    parts = normalized.split(os.sep)
    if '..' in parts:
        raise ValueError(f"Path traversal detected: {rel_path}")
    return normalized
```

---

## 5. Migration & Backwards Compatibility

### 5.1 CLI `--json` Behavior Change

| Version | Behavior |
|---------|----------|
| v1.x (current) | `{"run_id": "...", "report_markdown": "..."}` |
| v1.y (this PR) | Full structured JSON + deprecation warning if using old format |
| v2.0 | Remove deprecation warning, structured JSON is default |

**Migration:**
- Users relying on `report_markdown` key should use `--format markdown` and pipe
- New `--format json` is explicit structured output

### 5.2 Environment Variables (New)

| Variable | Default | Description |
|----------|---------|-------------|
| `PROGRESS_ENABLED` | `true` | Enable progress callbacks |
| `PROGRESS_RICH_ENABLED` | `true` | Enable Rich progress bar in CLI |
| `PROGRESS_FILE_ENABLED` | `true` | Enable JSONL trace (existing behavior) |

---

## 6. Operational Concerns

### 6.1 Memory/Backpressure

- **Bounded callback queue**: Max 1000 events per emitter (drop oldest)
- **Callback timeout**: 100ms per callback, skip on timeout
- **No memory accumulation**: Events are fire-and-forget

### 6.2 Connection Limits (N/A - No Server)

Not applicable - callback-based, no HTTP connections.

### 6.3 Archive Size Limits

- **Max artifact size**: 10MB per artifact (configurable via `EXPORT_MAX_ARTIFACT_BYTES`)
- **Max total archive**: 100MB (configurable via `EXPORT_MAX_ARCHIVE_BYTES`)
- **Streaming**: Archives are built in-memory but written in chunks

---

## 7. Security Concerns

### 7.1 Path Traversal (Export)

Mitigated by `_sanitize_path()` - see Section 4.3.

### 7.2 Log Injection

Events use structured JSON logging (already exists in `_log_graph_event()`).
All string fields are JSON-escaped.

### 7.3 SSRF (N/A - No Webhooks)

Webhooks deferred - no SSRF risk in this implementation.

---

## 8. Observability

### 8.1 Structured Logs

All progress events include:
- `trace_id`: W3C-compatible (already in node_trace.py)
- `run_id`: Workflow run identifier
- `span_id`: Per-node span

### 8.2 Metrics (Future)

Progress emitter could expose:
- `progress_events_emitted_total` (counter)
- `progress_callbacks_failed_total` (counter)
- `progress_callback_latency_seconds` (histogram)

---

## 9. Acceptance Criteria Checklist

### 9.1 Progress System
- [ ] `ProgressEmitter` can register/unregister callbacks
- [ ] `_log_graph_event()` calls emitter when set
- [ ] Rich progress bar shows in CLI with `--progress` flag
- [ ] Events include `progress_pct` calculated from node order

### 9.2 Output Formats
- [ ] `--format json` produces full IntegrationResult as JSON
- [ ] `--format yaml` produces full IntegrationResult as YAML
- [ ] `--format html` produces HTML report with styling
- [ ] `--format markdown` (default) unchanged behavior
- [ ] `--json` shows deprecation warning, maps to `--format json`

### 9.3 Code Export
- [ ] `integration-coworker export RUN_ID --format zip` creates valid ZIP
- [ ] `integration-coworker export RUN_ID --format tar.gz` creates valid tarball
- [ ] MANIFEST.json included with artifact metadata
- [ ] Path traversal attempts raise ValueError
- [ ] Large artifacts (>10MB) are rejected with clear error

### 9.4 Tests
- [ ] Unit tests for ProgressEmitter (register, emit, unregister)
- [ ] Unit tests for format renderers (JSON, YAML, HTML, Markdown)
- [ ] Unit tests for export (zip-slip prevention, manifest generation)
- [ ] Integration test: full run with progress callback captures events

---

## 10. PR Slices (Implementation Plan)

### PR1: Core Progress Infrastructure (This PR)
**Files:** `progress/events.py`, `progress/emitter.py`, `runtime.py` integration, tests
**Validation:** Unit tests pass, events appear in GRAPH_TRACE.jsonl

### PR2: CLI Progress Bar + Output Formats
**Files:** `progress/callbacks.py`, `output/formats.py`, `output/renderer.py`, `cli.py`
**Validation:** `--progress` shows Rich bar, `--format json` works

### PR3: Export Command
**Files:** `output/export.py`, `cli.py` (export command), tests
**Validation:** `integration-coworker export RUN_ID` creates valid archive

### PR4: Documentation + Migration Guide
**Files:** `docs/`, `CHANGELOG.md`
**Validation:** Docs updated, deprecation warnings documented

---

## 11. Downstream Impact Prediction

### 11.1 `runtime.py` Changes

**Callers:** `run_workflow()`, `_run_workflow_async()`
**Failure Modes:**
- Callback exception could break workflow → Mitigated by try/except in emitter
- Context var not set → Emitter checks for None, no-ops

**Remediation:** Emitter failures are logged but never raise

### 11.2 CLI `--json` Behavior Change

**Callers:** Any automation parsing CLI JSON output
**Failure Modes:**
- Scripts expecting `report_markdown` key get different structure

**Remediation:**
- Deprecation warning in v1.x
- Documentation in CHANGELOG
- `--format markdown | jq -Rs '{"report_markdown": .}'` as migration

---

## 12. Feature Prioritization

| Feature | Priority | Rationale |
|---------|----------|-----------|
| Progress Emitter Core | **MUST** | Foundation for all visibility |
| CLI `--progress` flag | **MUST** | Primary user feedback mechanism |
| `--format json/yaml` | **MUST** | Required for automation |
| `--format html` | **SHOULD** | Nice for sharing reports |
| Export ZIP/tar.gz | **SHOULD** | Enables code download without repo |
| Export single artifact | **NICE** | Edge case, can use archive |
| Webhooks | **DROP** | No persistence layer for retry/DLQ |
| Rate limiting | **DROP** | No public API server |
| RFC 7807 errors | **DROP** | CLI uses exit codes, not HTTP |

---

## Appendix A: Original ADR Content (Superseded)

The following content from the original ADR is preserved for reference but
has been superseded by the implementation spec above.

---

## 1. Streaming Progress Updates (Original)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Progress Notification System                      │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌─────────────┐      ┌──────────────────┐      ┌──────────────────┐   │
│   │ Graph Node  │ ──▶  │ ProgressEmitter  │ ──▶  │   Subscribers    │   │
│   │ Execution   │      │  (Pluggable)     │      │                  │   │
│   └─────────────┘      └──────────────────┘      │  • LogCallback   │   │
│                                ▲                  │  • SSECallback   │   │
│                                │                  │  • WSCallback    │   │
│   ┌─────────────┐              │                  │  • FileCallback  │   │
│   │ timed_node  │──────────────┘                  │  • WebhookCb     │   │
│   │  wrapper    │                                 └──────────────────┘   │
│   └─────────────┘                                                        │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Event Schema (ProgressEvent):**

```python
@dataclass
class ProgressEvent:
    """Standardized progress event for all subscribers."""
    run_id: str
    event_type: Literal["node.start", "node.end", "node.error", "node.skip", 
                        "workflow.start", "workflow.end", "workflow.error"]
    node_name: Optional[str]
    timestamp: str  # ISO 8601
    duration_ms: Optional[float]
    metadata: Dict[str, Any]  # NODE_METADATA fields
    progress_pct: Optional[float]  # 0.0-1.0 based on node order
    message: Optional[str]  # Human-readable status
```

**Integration Points:**

1. **Hook Location**: Modify `_log_graph_event()` in [runtime.py#L600](../src/integration_coworker/graph/runtime.py#L600)
2. **Callback Protocol**: Define `ProgressCallback = Callable[[ProgressEvent], None]`
3. **Subscriber Registry**: Thread-safe registry for multiple listeners

### 1.3 Implementation Plan

**Phase 1: Core Infrastructure (2 days)**

```python
# src/integration_coworker/progress/emitter.py
from typing import Callable, List, Protocol
from contextlib import contextmanager
import threading

class ProgressCallback(Protocol):
    def __call__(self, event: ProgressEvent) -> None: ...

class ProgressEmitter:
    """Thread-safe progress event emitter with pluggable subscribers."""
    
    _lock: threading.Lock
    _callbacks: List[ProgressCallback]
    
    def emit(self, event: ProgressEvent) -> None:
        """Emit event to all registered callbacks."""
        with self._lock:
            for cb in self._callbacks:
                try:
                    cb(event)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")
    
    def register(self, callback: ProgressCallback) -> None: ...
    def unregister(self, callback: ProgressCallback) -> None: ...

# Context var for per-run emitter
_progress_emitter: ContextVar[Optional[ProgressEmitter]] = ContextVar('progress_emitter')

@contextmanager
def progress_context(emitter: ProgressEmitter):
    """Set progress emitter for current workflow run."""
    token = _progress_emitter.set(emitter)
    try:
        yield
    finally:
        _progress_emitter.reset(token)
```

**Phase 2: Runtime Integration (1 day)**

Modify `_log_graph_event()` in [runtime.py](../src/integration_coworker/graph/runtime.py):

```python
def _log_graph_event(event_type: str, run_id: str, level: int, **kwargs) -> None:
    """Log graph lifecycle event and emit to progress subscribers."""
    # Existing logging code...
    logger.log(level, f"[{event_type}] {run_id}", extra=kwargs)
    
    # NEW: Emit to progress emitter if registered
    emitter = _progress_emitter.get(None)
    if emitter:
        progress_pct = _calculate_progress_pct(event_type, kwargs.get('node_name'))
        event = ProgressEvent(
            run_id=run_id,
            event_type=event_type.replace("graph.", ""),
            node_name=kwargs.get('node_name'),
            timestamp=datetime.utcnow().isoformat() + "Z",
            duration_ms=kwargs.get('duration_ms'),
            metadata=NODE_METADATA.get(kwargs.get('node_name', ''), {}),
            progress_pct=progress_pct,
            message=_format_progress_message(event_type, kwargs),
        )
        emitter.emit(event)
```

**Phase 3: Transport Adapters (3 days)**

| Adapter | Use Case | Implementation |
|---------|----------|----------------|
| `LogCallback` | CLI verbose mode | Rich console progress bar |
| `SSECallback` | REST API | FastAPI `EventSourceResponse` |
| `WebhookCallback` | External systems | Async HTTP POST to user URL |
| `FileCallback` | Debug/audit | Append to JSONL (existing) |

**Phase 4: API Endpoints (2 days)**

```python
# src/integration_coworker/api/progress.py
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

@router.get("/api/runs/{run_id}/progress")
async def stream_progress(run_id: str) -> EventSourceResponse:
    """Stream progress events via Server-Sent Events."""
    async def event_generator():
        queue = asyncio.Queue()
        
        def on_progress(event: ProgressEvent):
            queue.put_nowait(event)
        
        emitter = get_run_emitter(run_id)
        emitter.register(on_progress)
        
        try:
            while True:
                event = await queue.get()
                yield {"event": event.event_type, "data": event.to_json()}
                if event.event_type in ("workflow.end", "workflow.error"):
                    break
        finally:
            emitter.unregister(on_progress)
    
    return EventSourceResponse(event_generator())
```

### 1.4 CLI Integration

```python
# Add to cli.py run_integration()
@app.command("run")
def run_integration(
    spec_refs: List[str],
    task: str,
    # ...existing params...
    progress: bool = typer.Option(False, "--progress", "-P", help="Show live progress"),
):
    if progress:
        emitter = ProgressEmitter()
        emitter.register(RichProgressCallback())
        with progress_context(emitter):
            result = design_and_generate_integration(...)
    else:
        result = design_and_generate_integration(...)
```

### 1.5 Migration Path

1. **Week 1**: Core emitter + logging integration (backward compatible)
2. **Week 2**: SSE endpoint + CLI progress flag
3. **Week 3**: Streamlit real-time updates
4. **Week 4**: Webhook callback support

---

## 2. Output Format Adaptability

### 2.1 Current State Analysis

**Existing Output:**

| Component | Format | Location |
|-----------|--------|----------|
| `IntegrationResult` | Python dataclass | [api/types.py#L80+](../src/integration_coworker/api/types.py) |
| `report_markdown` | Markdown string | [build_report.py](../src/integration_coworker/graph/nodes/build_report.py) |
| CLI `--json` flag | JSON wrapper | [cli.py](../src/integration_coworker/cli.py) (wraps markdown in JSON) |

**Gap Analysis:**

- ❌ No `--format` flag for output type selection
- ❌ No content negotiation (`Accept` header) in API
- ❌ No HTML report generation
- ❌ No YAML output option
- ❌ `--json` only wraps markdown, doesn't produce structured JSON

**Evidence:**
```python
# Current --json behavior (cli.py):
if json_output:
    console.print_json(data={"result": result.report_markdown})
# This is NOT structured output - it's markdown inside JSON
```

### 2.2 Proposed Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      Output Format Pipeline                               │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   IntegrationResult                                                       │
│         │                                                                 │
│         ▼                                                                 │
│   ┌─────────────────────────────────────────────────────────────┐       │
│   │              OutputFormatter                                  │       │
│   │  ┌──────────────────────────────────────────────────────┐   │       │
│   │  │  format: OutputFormat                                 │   │       │
│   │  │  options: FormatOptions                               │   │       │
│   │  └──────────────────────────────────────────────────────┘   │       │
│   │                         │                                    │       │
│   │           ┌─────────────┼─────────────┐                     │       │
│   │           ▼             ▼             ▼                     │       │
│   │     ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │       │
│   │     │ Markdown │ │   JSON   │ │   YAML   │ │   HTML   │    │       │
│   │     │ Renderer │ │ Renderer │ │ Renderer │ │ Renderer │    │       │
│   │     └──────────┘ └──────────┘ └──────────┘ └──────────┘    │       │
│   └─────────────────────────────────────────────────────────────┘       │
│                         │                                                │
│                         ▼                                                │
│                  Formatted Output                                        │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.3 Implementation Plan

**Phase 1: Core Types (1 day)**

```python
# src/integration_coworker/output/formats.py
from enum import Enum
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
import json
import yaml

class OutputFormat(str, Enum):
    """Supported output formats."""
    MARKDOWN = "markdown"
    JSON = "json"
    YAML = "yaml"
    HTML = "html"

@dataclass
class FormatOptions:
    """Options for output formatting."""
    include_artifacts: bool = True      # Include code_artifacts in output
    include_schemas: bool = True        # Include OpenAPI schemas
    include_trace: bool = False         # Include node_timings/completed_steps
    artifact_content: bool = True       # Include artifact content (large!)
    pretty_print: bool = True           # Indent JSON/YAML
    html_template: str = "default"      # HTML template name
```

**Phase 2: Format Renderers (2 days)**

```python
# src/integration_coworker/output/renderer.py
from integration_coworker.api.types import IntegrationResult

class OutputRenderer:
    """Base class for output format renderers."""
    
    def render(self, result: IntegrationResult, options: FormatOptions) -> str:
        raise NotImplementedError

class JSONRenderer(OutputRenderer):
    """Render IntegrationResult as structured JSON."""
    
    def render(self, result: IntegrationResult, options: FormatOptions) -> str:
        data = self._result_to_dict(result, options)
        indent = 2 if options.pretty_print else None
        return json.dumps(data, indent=indent, default=str)
    
    def _result_to_dict(self, result: IntegrationResult, options: FormatOptions) -> Dict:
        """Convert result to serializable dict with option filtering."""
        base = {
            "run_id": result.run_id,
            "task": result.task,
            "status": "success" if not result.errors else "failed",
            "provider_code": result.provider_code,
            "errors": result.errors,
        }
        
        if options.include_artifacts:
            artifacts = []
            for a in result.code_artifacts:
                artifact_dict = {
                    "id": a.id,
                    "artifact_type": a.artifact_type,
                    "language": a.language,
                    "module_name": a.module_name,
                    "rel_path": a.rel_path,
                }
                if options.artifact_content:
                    artifact_dict["content"] = a.content
                artifacts.append(artifact_dict)
            base["code_artifacts"] = artifacts
        
        if options.include_schemas:
            base["endpoints"] = [asdict(e) for e in result.endpoints]
            base["schemas"] = [asdict(s) for s in result.schemas]
        
        if options.include_trace:
            base["completed_steps"] = result.completed_steps
            base["node_timings"] = getattr(result, 'node_timings', {})
        
        return base

class YAMLRenderer(OutputRenderer):
    """Render IntegrationResult as YAML."""
    
    def render(self, result: IntegrationResult, options: FormatOptions) -> str:
        data = JSONRenderer()._result_to_dict(result, options)
        return yaml.dump(data, default_flow_style=False, sort_keys=False)

class HTMLRenderer(OutputRenderer):
    """Render IntegrationResult as HTML report."""
    
    def render(self, result: IntegrationResult, options: FormatOptions) -> str:
        # Convert markdown to HTML using existing report_markdown
        import markdown
        md = result.report_markdown
        html_content = markdown.markdown(md, extensions=['tables', 'fenced_code'])
        return self._wrap_html(html_content, result, options)
    
    def _wrap_html(self, content: str, result: IntegrationResult, options: FormatOptions) -> str:
        template = self._load_template(options.html_template)
        return template.format(
            title=f"Integration Report - {result.run_id}",
            content=content,
            run_id=result.run_id,
            status="success" if not result.errors else "failed",
        )

class MarkdownRenderer(OutputRenderer):
    """Pass-through renderer for existing markdown output."""
    
    def render(self, result: IntegrationResult, options: FormatOptions) -> str:
        return result.report_markdown
```

**Phase 3: CLI Integration (1 day)**

```python
# cli.py modifications
from integration_coworker.output.formats import OutputFormat, FormatOptions
from integration_coworker.output.renderer import get_renderer

@app.command("run")
def run_integration(
    spec_refs: List[str],
    task: str,
    # ...existing params...
    format: OutputFormat = typer.Option(
        OutputFormat.MARKDOWN, 
        "--format", "-f",
        help="Output format: markdown, json, yaml, html"
    ),
    # Keep --json as shorthand for --format json
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON (shorthand for --format json)"),
    include_artifacts: bool = typer.Option(True, "--include-artifacts/--no-artifacts"),
    include_trace: bool = typer.Option(False, "--include-trace", help="Include execution trace"),
):
    # Resolve format
    effective_format = OutputFormat.JSON if json_output else format
    
    # Run workflow
    result = design_and_generate_integration(...)
    
    # Render output
    options = FormatOptions(
        include_artifacts=include_artifacts,
        include_trace=include_trace,
    )
    renderer = get_renderer(effective_format)
    output = renderer.render(result, options)
    
    # Output based on format
    if effective_format == OutputFormat.JSON:
        console.print_json(output)
    elif effective_format == OutputFormat.HTML:
        # Write to file
        output_path = Path(f"{result.run_id}.html")
        output_path.write_text(output)
        console.print(f"[green]HTML report written to {output_path}[/green]")
    else:
        console.print(output)
```

**Phase 4: API Content Negotiation (1 day)**

```python
# src/integration_coworker/api/entrypoint.py
from fastapi import Request
from fastapi.responses import Response, JSONResponse

MIME_TO_FORMAT = {
    "application/json": OutputFormat.JSON,
    "text/yaml": OutputFormat.YAML,
    "application/yaml": OutputFormat.YAML,
    "text/html": OutputFormat.HTML,
    "text/markdown": OutputFormat.MARKDOWN,
}

@router.post("/api/integrations")
async def create_integration(
    request: Request,
    body: IntegrationRequest,
) -> Response:
    # Content negotiation
    accept = request.headers.get("Accept", "application/json")
    output_format = MIME_TO_FORMAT.get(accept, OutputFormat.JSON)
    
    # Run workflow
    result = await design_and_generate_integration_async(...)
    
    # Render response
    renderer = get_renderer(output_format)
    content = renderer.render(result, FormatOptions())
    
    return Response(
        content=content,
        media_type=accept if accept in MIME_TO_FORMAT else "application/json"
    )
```

### 2.4 Backward Compatibility

| Existing Usage | New Behavior |
|----------------|--------------|
| `bd run` (no flags) | Unchanged - markdown output |
| `bd run --json` | Now produces structured JSON (breaking change, document) |
| API call | Default to JSON, respects Accept header |

**Migration:** Add deprecation warning to old `--json` behavior for one release.

---

## 3. Code Export Endpoint

### 3.1 Current State Analysis

**Existing Code Artifact Storage:**

```python
# From domain/models.py
@dataclass
class CodeArtifact:
    id: str
    task_id: str
    artifact_type: str  # "implementation", "tests", "types"
    language: str       # "python", "typescript"
    module_name: str    # "stripe_client"
    rel_path: str       # "src/integrations/stripe_client.py"
    content: str        # Full source code
```

**Gap Analysis:**

- ❌ No `/api/runs/{run_id}/export` endpoint
- ❌ No archive generation (.zip, .tar.gz)
- ❌ No content-disposition headers for download
- ❌ No manifest file for artifact metadata
- ✅ `CodeArtifact.rel_path` provides directory structure

### 3.2 Proposed Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Code Export System                                │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   GET /api/runs/{run_id}/export                                          │
│   ├── ?format=zip          → application/zip                             │
│   ├── ?format=tar.gz       → application/gzip                            │
│   ├── ?format=json         → application/json (artifact list)            │
│   └── ?artifact_id={id}    → individual file download                    │
│                                                                          │
│   Archive Structure:                                                      │
│   ├── MANIFEST.json        # Metadata about all artifacts                │
│   ├── README.md            # Generated usage instructions                │
│   ├── src/                                                               │
│   │   └── integrations/                                                  │
│   │       ├── stripe_client.py                                           │
│   │       └── stripe_client_test.py                                      │
│   └── tests/                                                             │
│       └── test_stripe.py                                                 │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Implementation Plan

**Phase 1: Export Service (2 days)**

```python
# src/integration_coworker/api/export.py
from dataclasses import dataclass
from typing import List, Optional, Literal
from pathlib import PurePosixPath
import zipfile
import tarfile
import io
import json

@dataclass
class ExportManifest:
    """Manifest describing exported artifacts."""
    run_id: str
    export_timestamp: str
    task_description: str
    provider_code: str
    artifact_count: int
    artifacts: List[dict]  # {id, type, language, rel_path, size_bytes}
    
    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

class CodeExporter:
    """Generate downloadable archives from IntegrationResult."""
    
    def export_zip(self, result: IntegrationResult) -> bytes:
        """Create ZIP archive with artifacts and manifest."""
        buffer = io.BytesIO()
        
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            manifest = self._build_manifest(result)
            zf.writestr("MANIFEST.json", manifest.to_json())
            zf.writestr("README.md", self._generate_readme(result))
            
            for artifact in result.code_artifacts:
                # Use rel_path for directory structure
                path = artifact.rel_path or f"{artifact.module_name}.{self._ext(artifact.language)}"
                zf.writestr(path, artifact.content)
        
        buffer.seek(0)
        return buffer.getvalue()
    
    def export_tar_gz(self, result: IntegrationResult) -> bytes:
        """Create tar.gz archive with artifacts and manifest."""
        buffer = io.BytesIO()
        
        with tarfile.open(fileobj=buffer, mode='w:gz') as tf:
            manifest = self._build_manifest(result)
            self._add_string_to_tar(tf, "MANIFEST.json", manifest.to_json())
            self._add_string_to_tar(tf, "README.md", self._generate_readme(result))
            
            for artifact in result.code_artifacts:
                path = artifact.rel_path or f"{artifact.module_name}.{self._ext(artifact.language)}"
                self._add_string_to_tar(tf, path, artifact.content)
        
        buffer.seek(0)
        return buffer.getvalue()
    
    def get_single_artifact(self, result: IntegrationResult, artifact_id: str) -> Optional[tuple]:
        """Get single artifact by ID. Returns (filename, content, mime_type)."""
        for artifact in result.code_artifacts:
            if artifact.id == artifact_id:
                filename = PurePosixPath(artifact.rel_path).name
                mime = self._get_mime_type(artifact.language)
                return (filename, artifact.content, mime)
        return None
    
    def _build_manifest(self, result: IntegrationResult) -> ExportManifest:
        return ExportManifest(
            run_id=result.run_id,
            export_timestamp=datetime.utcnow().isoformat() + "Z",
            task_description=result.task,
            provider_code=result.provider_code or "unknown",
            artifact_count=len(result.code_artifacts),
            artifacts=[
                {
                    "id": a.id,
                    "type": a.artifact_type,
                    "language": a.language,
                    "rel_path": a.rel_path,
                    "size_bytes": len(a.content.encode('utf-8')),
                }
                for a in result.code_artifacts
            ],
        )
    
    def _generate_readme(self, result: IntegrationResult) -> str:
        return f"""# Generated Integration Code

**Run ID:** {result.run_id}
**Task:** {result.task}
**Provider:** {result.provider_code}
**Generated:** {datetime.utcnow().isoformat()}Z

## Contents

| File | Type | Language |
|------|------|----------|
{chr(10).join(f"| `{a.rel_path}` | {a.artifact_type} | {a.language} |" for a in result.code_artifacts)}

## Usage

See individual files for usage instructions.
"""
```

**Phase 2: API Endpoint (1 day)**

```python
# src/integration_coworker/api/routes/export.py
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from integration_coworker.persistence.runs import get_run_result

router = APIRouter(tags=["export"])

ExportFormat = Literal["zip", "tar.gz", "json"]

@router.get("/api/runs/{run_id}/export")
async def export_code(
    run_id: str,
    format: ExportFormat = Query("zip", description="Export format"),
    artifact_id: Optional[str] = Query(None, description="Single artifact ID"),
) -> StreamingResponse:
    """
    Export generated code artifacts.
    
    - **format=zip**: ZIP archive with all artifacts + manifest
    - **format=tar.gz**: Gzipped tarball with all artifacts + manifest  
    - **format=json**: JSON list of artifact metadata
    - **artifact_id**: Download single artifact by ID
    """
    # Fetch run result from persistence
    result = await get_run_result(run_id)
    if not result:
        raise HTTPException(404, f"Run {run_id} not found")
    
    if not result.code_artifacts:
        raise HTTPException(404, f"Run {run_id} has no code artifacts")
    
    exporter = CodeExporter()
    
    # Single artifact download
    if artifact_id:
        artifact = exporter.get_single_artifact(result, artifact_id)
        if not artifact:
            raise HTTPException(404, f"Artifact {artifact_id} not found in run {run_id}")
        filename, content, mime_type = artifact
        return StreamingResponse(
            iter([content.encode('utf-8')]),
            media_type=mime_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    # Full archive export
    if format == "json":
        manifest = exporter._build_manifest(result)
        return JSONResponse(content=asdict(manifest))
    elif format == "zip":
        archive = exporter.export_zip(result)
        return StreamingResponse(
            iter([archive]),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={run_id}.zip"}
        )
    elif format == "tar.gz":
        archive = exporter.export_tar_gz(result)
        return StreamingResponse(
            iter([archive]),
            media_type="application/gzip",
            headers={"Content-Disposition": f"attachment; filename={run_id}.tar.gz"}
        )
```

**Phase 3: CLI Export Command (1 day)**

```python
# Add to cli.py
@app.command("export")
def export_code(
    run_id: str = typer.Argument(..., help="Run ID to export"),
    output: Path = typer.Option(None, "--output", "-o", help="Output path"),
    format: str = typer.Option("zip", "--format", "-f", help="Export format: zip, tar.gz"),
):
    """Export generated code from a previous run."""
    result = get_run_result(run_id)
    if not result:
        raise typer.BadParameter(f"Run {run_id} not found")
    
    exporter = CodeExporter()
    
    if format == "zip":
        data = exporter.export_zip(result)
        ext = ".zip"
    else:
        data = exporter.export_tar_gz(result)
        ext = ".tar.gz"
    
    output_path = output or Path(f"{run_id}{ext}")
    output_path.write_bytes(data)
    console.print(f"[green]Exported to {output_path}[/green]")
```

---

## 4. Additional Production Features Audit

### 4.1 Rate Limiting

**Current State:** ❌ No rate limiting implemented

**Recommendation:** Add token bucket rate limiter at API layer:

```python
# src/integration_coworker/api/middleware/rate_limit.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Apply to API routes
@router.post("/api/integrations")
@limiter.limit("10/minute")  # 10 workflow runs per minute per IP
async def create_integration(...): ...

@router.get("/api/runs/{run_id}/export")
@limiter.limit("30/minute")  # 30 exports per minute per IP
async def export_code(...): ...
```

**Priority:** P2 (before public deployment)

### 4.2 Pagination

**Current State:** ❌ No pagination for list endpoints

**Recommendation:** Cursor-based pagination for run lists:

```python
@dataclass
class PaginatedResponse:
    items: List[Any]
    cursor: Optional[str]
    has_more: bool

@router.get("/api/runs")
async def list_runs(
    limit: int = Query(20, le=100),
    cursor: Optional[str] = Query(None),
) -> PaginatedResponse:
    runs = await get_runs(limit=limit + 1, cursor=cursor)
    has_more = len(runs) > limit
    items = runs[:limit]
    next_cursor = items[-1].run_id if has_more else None
    return PaginatedResponse(items=items, cursor=next_cursor, has_more=has_more)
```

**Priority:** P3 (nice-to-have for V1)

### 4.3 Caching Headers

**Current State:** ❌ No Cache-Control headers

**Recommendation:** Immutable caching for completed runs:

```python
@router.get("/api/runs/{run_id}")
async def get_run(run_id: str, response: Response):
    result = await get_run_result(run_id)
    
    # Completed runs are immutable
    if result.status == "completed":
        response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        response.headers["ETag"] = f'"{result.run_id}"'
    else:
        response.headers["Cache-Control"] = "no-cache"
    
    return result
```

**Priority:** P3

### 4.4 Audit Logging

**Current State:** ✅ Partial - `GRAPH_TRACE.jsonl` captures node events

**Recommendation:** Add API-level audit logging:

```python
# src/integration_coworker/api/middleware/audit.py
import structlog

audit_log = structlog.get_logger("audit")

@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    
    audit_log.info(
        "api_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration * 1000,
        user_agent=request.headers.get("user-agent"),
        request_id=request.headers.get("x-request-id"),
    )
    return response
```

**Priority:** P1 (required for production)

### 4.5 Standardized Error Format

**Current State:** ❌ Inconsistent error responses

**Recommendation:** RFC 7807 Problem Details:

```python
# src/integration_coworker/api/errors.py
from fastapi import HTTPException
from fastapi.responses import JSONResponse

@dataclass
class ProblemDetail:
    """RFC 7807 Problem Details for HTTP APIs."""
    type: str  # URI reference for error type
    title: str  # Short human-readable summary
    status: int  # HTTP status code
    detail: Optional[str] = None  # Explanation specific to this occurrence
    instance: Optional[str] = None  # URI reference for this occurrence
    errors: Optional[List[dict]] = None  # Validation errors

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ProblemDetail(
            type=f"/errors/{exc.status_code}",
            title=exc.detail,
            status=exc.status_code,
            instance=str(request.url),
        ).__dict__,
        media_type="application/problem+json",
    )
```

**Priority:** P1

### 4.6 Webhook Notifications

**Current State:** ❌ Not implemented

**Recommendation:** Add webhook registration and delivery:

```python
# src/integration_coworker/webhooks/service.py
@dataclass
class WebhookSubscription:
    id: str
    url: str
    events: List[str]  # ["run.completed", "run.failed"]
    secret: str  # For HMAC signature

class WebhookService:
    async def deliver(self, event_type: str, payload: dict):
        """Deliver webhook to all subscribers for this event."""
        subscriptions = await self.get_subscriptions(event_type)
        
        for sub in subscriptions:
            signature = hmac.new(
                sub.secret.encode(),
                json.dumps(payload).encode(),
                hashlib.sha256
            ).hexdigest()
            
            async with httpx.AsyncClient() as client:
                await client.post(
                    sub.url,
                    json=payload,
                    headers={
                        "X-Webhook-Signature": signature,
                        "X-Event-Type": event_type,
                    },
                    timeout=30.0,
                )

# Integration with progress system
def on_workflow_complete(event: ProgressEvent):
    if event.event_type == "workflow.end":
        webhook_service.deliver("run.completed", {
            "run_id": event.run_id,
            "status": "success",
            "timestamp": event.timestamp,
        })
```

**Priority:** P2

### 4.7 Bulk Operations

**Current State:** ❌ Single-run operations only

**Recommendation:** Add batch endpoint for multiple spec processing:

```python
@router.post("/api/integrations/batch")
async def create_batch_integrations(
    requests: List[IntegrationRequest],
    parallel: bool = Query(False),
) -> List[IntegrationResult]:
    """
    Process multiple integration requests.
    
    - **parallel=false**: Sequential processing (default)
    - **parallel=true**: Concurrent processing (higher resource usage)
    """
    if parallel:
        results = await asyncio.gather(*[
            design_and_generate_integration_async(**r.dict())
            for r in requests
        ])
    else:
        results = []
        for r in requests:
            results.append(await design_and_generate_integration_async(**r.dict()))
    
    return results
```

**Priority:** P3

---

## 5. Implementation Priority Matrix

| Feature | Priority | Effort | Dependencies | Sprint |
|---------|----------|--------|--------------|--------|
| Progress Emitter Core | P0 | 2d | None | 1 |
| Output Format Enum | P0 | 1d | None | 1 |
| Code Export Endpoint | P1 | 3d | None | 1 |
| SSE Progress Streaming | P1 | 2d | Progress Emitter | 2 |
| CLI `--format` Flag | P1 | 1d | Output Format Enum | 2 |
| API Content Negotiation | P1 | 1d | Output Format Enum | 2 |
| Audit Logging | P1 | 1d | None | 2 |
| Error Format (RFC 7807) | P1 | 1d | None | 2 |
| Rate Limiting | P2 | 1d | None | 3 |
| Webhook Notifications | P2 | 3d | Progress Emitter | 3 |
| CLI Progress Bar | P2 | 1d | Progress Emitter | 3 |
| Pagination | P3 | 1d | None | 4 |
| Caching Headers | P3 | 0.5d | None | 4 |
| Bulk Operations | P3 | 2d | None | 4 |

---

## 6. Risk Assessment

| Risk | Mitigation |
|------|------------|
| Breaking `--json` behavior | Add deprecation warning, document in CHANGELOG |
| SSE connection limits | Use connection pooling, configurable max connections |
| Archive size limits | Add `max_artifact_size` config, stream large archives |
| Webhook delivery failures | Retry with exponential backoff, DLQ for failures |
| Rate limiting false positives | Add API key authentication bypass for power users |

---

## 7. References

- [runtime.py](../src/integration_coworker/graph/runtime.py) - Node lifecycle and event logging
- [node_trace.py](../src/integration_coworker/graph/node_trace.py) - Step-level tracing
- [api/types.py](../src/integration_coworker/api/types.py) - IntegrationResult definition
- [api/recovery.py](../src/integration_coworker/api/recovery.py) - Existing resume support
- [domain/models.py](../src/integration_coworker/domain/models.py) - CodeArtifact structure

---

## Decision

**Approved for implementation in phases:**

1. **Sprint 1 (Week 1-2)**: Core infrastructure (Progress Emitter, Output Format, Export)
2. **Sprint 2 (Week 3-4)**: API integration (SSE, Content Negotiation, CLI flags)
3. **Sprint 3 (Week 5-6)**: Production hardening (Rate limiting, Webhooks, Audit)
4. **Sprint 4 (Week 7-8)**: Polish (Pagination, Caching, Bulk)

**Backward Compatibility Commitment:** All changes will be additive. Existing CLI commands and API contracts will continue to work with deprecation warnings where behavior changes.
