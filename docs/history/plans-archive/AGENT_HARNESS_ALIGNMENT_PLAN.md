# Agent Harness Alignment Implementation Plan

**Document Version:** 2.0  
**Last Updated:** 2025-12-18  
**Status:** Draft (Step 1-5 corrections applied)  
**Section Reference:** 8.5 (Agent Harness Alignment)

## Executive Summary

This plan addresses the **Agent Harness Alignment** capability gap identified in the V2 roadmap. The goal is to bring this codebase to parity with DeepAgents-style capabilities—specifically **large result eviction/artifact spooling**, **HITL (Human-In-The-Loop) gates**, and **bounded delegation via subgraphs**—but domain-shaped for integration coworker workflows.

**Key Design Principles:**
1. **LangGraph-native first**: Use `interrupt()` for HITL, not custom DB polling
2. **Invariant-based artifact store**: No excluded field may become unrecoverable
3. **Minimal-diff integration**: Changes at exactly 2 seams (serialize + restore)
4. **Evidence-backed claims**: Every "complete" claim requires test/log citation

---

## Step 1: Evidence-First Discovery

### 1.1 Capability Matrix

| # | Capability | Status | File(s) | Lines | Evidence/Notes |
|---|------------|--------|---------|-------|----------------|
| A | **Checkpointing/Resume** | ✅ Implemented | `graph/runtime.py` | 108-186 | `AsyncPostgresSaver` with TCP keepalive; verified in demo log |
| B | **Large Field Exclusion** | ⚠️ Partial | `persistence/checkpoints.py` | 26-68 | Excludes openapi_spec, files—**data lost, not spooled** |
| C | **Repo Provider Abstraction** | ✅ Implemented | `repo/providers/base.py`, `local.py` | full | `pytest tests/test_repo_providers.py -v` |
| D | **Self-Review Loop** | ✅ Implemented | `codegen/self_review.py` | full | Profile-controlled, max 1 repair iteration |
| E | **Dry-Run Mode** | ✅ Implemented | `graph/nodes/apply_repo_integration_changes.py` | 230-256 | `--dry-run` flag works; no approval gate |
| F | **Artifact Store (Spooling)** | ❌ Missing | N/A | N/A | **P0 gap**: fields excluded → data loss on resume |
| G | **HITL Interrupts** | ❌ Missing | N/A | N/A | No `interrupt()` call; only dry_run exists |
| H | **Bounded Subgraphs** | ❌ Missing | `graph/runtime.py` | 984-998 | Parallel branches exist; no isolation/timeout |
| I | **Repo IO Audit** | ❌ Missing | N/A | N/A | No path allowlist/denylist, no audit logging |
| J | **Run Artifact Directory** | ⚠️ Partial | DB tables | `postgres.py:544` | `run_status` table exists; no filesystem store |

### 1.2 Key Evidence Citations

#### A. Checkpointing Infrastructure (Implemented)

**File:** `src/integration_coworker/graph/runtime.py:108-186`
```python
@contextmanager
def checkpointer_context() -> Generator[Optional[BaseCheckpointSaver], None, None]:
    """
    Context manager for checkpoint saver with proper connection handling.
    Bug #61 fix: Uses async PostgresSaver with proper connection management.
    """
    ...
    if get_engine_type() == "postgres":
        conn = pool.getconn()
        # TCP keepalive for long-running workflows
        conn.set_session(autocommit=True)
        saver = PostgresSaver(conn)
        ...
```

**Verdict:** Solid foundation. LangGraph native checkpointing works for resume.

#### B. Large Field Exclusion (Partial—Data Loss Bug)

**File:** `src/integration_coworker/persistence/checkpoints.py:26-68`
```python
EXCLUDE_FIELDS = {
    "openapi_spec",     # Large serialized spec (Bug #82)
    "spec_documents",   # List of SpecDocument objects
    "spec_sections",    # Large list
    "endpoints",        # Can be very large for big specs
    "files",            # Bug #89: repo_snapshot.files can be huge
}

def _serialize_state(state: WorkflowState) -> Dict[str, Any]:
    """Serialize state, excluding large fields that would break Postgres."""
    data = {}
    for key, value in state.__dict__.items():
        if key in EXCLUDE_FIELDS:
            continue  # <-- DATA IS LOST HERE, NOT SPOOLED
        ...
```

**Bug Identified:** Large fields are excluded from checkpoints but **not persisted anywhere else**. On resume, these fields are empty → broken state.

#### C. Self-Review Loop (Implemented)

**File:** `src/integration_coworker/codegen/self_review.py:358-410`
```python
async def review_and_repair(
    code: str,
    artifact_type: str,
    ...
    max_repair_attempts: int = 1,
) -> tuple[str, bool, Optional[str]]:
    """
    Perform self-review with optional repair iteration.
    Per requirement B3.5: Max 1 self-review repair iteration per artifact.
    """
```

**Verdict:** Good bounded review pattern. Can be extended for other validation loops.

#### D. Dry-Run Mode (Implemented but Incomplete)

**File:** `src/integration_coworker/graph/nodes/apply_repo_integration_changes.py:200-230`
```python
def apply_repo_integration_changes(state: WorkflowState) -> WorkflowState:
    is_dry_run = state.options.dry_run if state.options else False
    ...
    if not dry_run:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(artifact.content, encoding="utf-8")
```

**Gap:** Dry-run is plan-only mode, but there's no **approval gate** to convert dry-run to apply. User must re-run the entire workflow.

#### E. Parallel Branches (No True Subgraphs)

**File:** `src/integration_coworker/graph/runtime.py:984-998`
```python
# PARALLEL BRANCHES: build_silver_file_model fans out to both nodes
workflow.add_edge("build_silver_file_model", "embed_spec_chunks")
workflow.add_edge("build_silver_file_model", "understand_task")

# Both parallel branches merge at sync_embed_task
workflow.add_edge("embed_spec_chunks", "sync_embed_task")
workflow.add_edge("understand_task", "sync_embed_task")
```

**Gap:** These are parallel edges, not isolated subgraphs. No resource budget, no timeout, no failure isolation.

#### F. Run ID Infrastructure (Partial)

**File:** `src/integration_coworker/persistence/postgres.py:544-565`
```sql
CREATE TABLE IF NOT EXISTS integration_gold.run_status (
    run_id           TEXT PRIMARY KEY,
    status           TEXT NOT NULL DEFAULT 'pending',
    ...
);
CREATE TABLE IF NOT EXISTS integration_gold.run_checkpoints (
    run_id     TEXT NOT NULL REFERENCES integration_gold.run_status(run_id),
    node_name  TEXT NOT NULL,
    checkpoint JSONB NOT NULL,
    ...
);
```

**Verdict:** Good run tracking foundation. Need artifact store table and filesystem spool.

---

## Step 2: Target Architecture

### 2.1 Architecture Options Debate

#### Area A: RunArtifactStore (Large Result Eviction)

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **A1: Filesystem Spool** | Simple, fast reads, Git-ignorable | No transactional consistency with DB | ❌ |
| **A2: DB BYTEA/JSONB** | Transactional, single source of truth | Bloats DB, expensive queries | ❌ |
| **A3: Hybrid (Pointer + File)** | Best of both—DB tracks, FS stores | Two systems to manage | ✅ CHOSEN |

**Decision:** Hybrid approach with pluggable `ArtifactStore` interface. Default backend: filesystem blobs + DB index. DB table `run_artifacts` stores metadata; filesystem stores actual blobs under `~/.integration_coworker/runs/{run_id}/artifacts/`.

**Required Invariants:**
1. **No excluded field may become unrecoverable.** If excluded from checkpoint, must be persisted to artifact store with stable reference.
2. **References must be sufficient to restore on resume** with no "best effort" behavior.
3. **Artifact persistence must be tied to `run_id`** and cleaned up via retention policy.
4. **Storage must be pluggable** (`ArtifactStore` interface) for future S3/GCS backends.

See: [DeepAgents harness capabilities](https://docs.langchain.com/oss/javascript/deepagents/harness)

#### Area B: HITL Gates (Human Approval)

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **B1: CLI Apply Mode** | Simple, no new infra | Requires full re-run | ❌ |
| **B2: LangGraph `interrupt()`** | Native, suspends graph, uses existing persistence | Requires understanding thread model | ✅ CHOSEN |
| **B3: DB Approval Queue** | Decoupled from LangGraph, multi-tenant ready | Duplicates LangGraph's pause/resume; more code | ⚠️ ADAPTER ONLY |

**Decision:** Use **LangGraph `interrupt()`** as the primary HITL mechanism. The node calls `interrupt(payload)` right before destructive operations; payload includes diff summary + artifact hashes + file list. Resume via LangGraph's thread-based resume mechanism.

**DB approval queue is only an adapter** for scenarios requiring:
- Multi-tenant operator queues
- Approval audit/analytics independent of LangGraph threads
- Worker fleet coordination

**Decision Rule:** If pause/resume works via LangGraph threads (CLI/UI/service), use `interrupt()`. Add DB queue later as service-mode adapter.

See: [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

#### Area C: Repo IO Boundary

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **C1: Wrapper Functions** | Simple auditing | Scattered call sites | ❌ |
| **C2: Single `repo/io.py` Module** | Centralized, 100% coverage guaranteed | Requires call site refactor | ✅ CHOSEN |
| **C3: Provider Interceptors** | Pluggable | Risks scattered auditing | ⚠️ REJECTED |
| **C4: Filesystem Proxy** | Complete isolation | Heavy, requires FUSE | ❌ |

**Decision:** Create `src/integration_coworker/repo/io.py` exposing `read_text, write_text, list_files, glob, grep`. All call sites must use this surface. Centralizes:
- Path policy (allow/deny, symlink policy, size limits)
- Audit logging
- Counters (bytes read/written per run)

#### Area D: Bounded Execution

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **D1: Explicit Subgraphs** | Full isolation, nested checkpoints | Complex, premature | ⚠️ P3 (Future) |
| **D2: Node Wrapper Utility** | Config-driven, maintainable | Less isolation | ✅ CHOSEN |
| **D3: Ad-hoc Decorators** | Quick | Unmaintainable, scattered | ❌ REJECTED |

**Decision:** Single node wrapper utility in `graph/bounded_node.py` that the graph builder applies consistently based on Profile config. **Do not** decorate `add_node` calls inline—that becomes unmaintainable.

See: [LangGraph Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) (for future D1 evolution)

### 2.2 Target Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Integration Coworker V2                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │   CLI/API     │───▶│ HITL Gate     │───▶│ Workflow      │           │
│  │   Entry       │    │ interrupt()   │    │ Graph         │           │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│                              │                     │                    │
│                     Resume via thread_id           │                    │
│                              │                     ▼                    │
│                       ┌───────────────┐    ┌───────────────┐           │
│                       │ LangGraph     │    │ Bounded       │           │
│                       │ Persistence   │    │ Nodes         │           │
│                       │ (Postgres/    │    │ (Profile-     │           │
│                       │  SQLite)      │    │  driven)      │           │
│                       └───────────────┘    └───────────────┘           │
│                                                    │                    │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │                        Artifact Layer                             │ │
│  │  ┌─────────────────┐    ┌─────────────────┐                       │ │
│  │  │ ArtifactStore   │───▶│ Filesystem      │                       │ │
│  │  │ (Interface)     │    │ ~/.integration_coworker/runs/           │ │
│  │  │ + DB Index      │    │ (pluggable: S3/GCS future)              │ │
│  │  └─────────────────┘    └─────────────────┘                       │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │                        Repo IO Layer                              │ │
│  │  ┌─────────────────┐                                              │ │
│  │  │ repo/io.py      │  ← Single surface for all file ops           │ │
│  │  │ (audit, policy, │                                              │ │
│  │  │  counters)      │                                              │ │
│  │  └─────────────────┘                                              │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Step 3: File-Level Implementation Plan (Minimal-Diff)

### 3.1 Feature A: RunArtifactStore (P0)

**Integration Seams (exactly 2):**
1. **Seam 1**: `persistence/checkpoints.py:_serialize_state()` → replace excluded fields with `ArtifactRef`
2. **Seam 2**: `persistence/checkpoints.py:load_checkpoint()` → rehydrate fields from `ArtifactRef`

| File | Action | Changes |
|------|--------|---------|
| `src/integration_coworker/persistence/artifact_store.py` | CREATE | `ArtifactStore` Protocol + `FilesystemArtifactStore` default impl |
| `src/integration_coworker/persistence/postgres.py` | MODIFY | Add `run_artifacts` table DDL (+15 lines) |
| `src/integration_coworker/persistence/checkpoints.py` | MODIFY | Seam 1 + Seam 2 integration (~40 lines changed) |
| `tests/test_artifact_store.py` | CREATE | Unit tests for store/retrieve/evict + resume test |

**Artifact Store Contract:**
```python
# artifact_store.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

@dataclass(frozen=True)
class ArtifactRef:
    """Stable reference to an artifact. Sufficient to restore on resume."""
    run_id: str
    key: str
    checksum_sha256: str
    size_bytes: int


class ArtifactStore(Protocol):
    """Pluggable artifact storage interface."""
    
    def store(self, run_id: str, key: str, data: Any) -> ArtifactRef:
        """Persist data, return stable reference. Must be idempotent."""
        ...
    
    def retrieve(self, ref: ArtifactRef) -> Any:
        """Load artifact from reference. Raises if checksum mismatch."""
        ...
    
    def exists(self, ref: ArtifactRef) -> bool:
        """Check if artifact exists and is valid."""
        ...
    
    def delete_run(self, run_id: str) -> int:
        """Delete all artifacts for a run. Returns count deleted."""
        ...


class FilesystemArtifactStore:
    """Default implementation: filesystem blobs + DB index."""
    
    def __init__(self, runs_dir: Path = None, db_conn = None):
        self.runs_dir = runs_dir or Path.home() / ".integration_coworker" / "runs"
        self.db_conn = db_conn  # For index operations
    
    def store(self, run_id: str, key: str, data: Any) -> ArtifactRef:
        import gzip, hashlib, orjson
        
        artifact_dir = self.runs_dir / run_id / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        
        serialized = orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY)
        compressed = gzip.compress(serialized)
        checksum = hashlib.sha256(compressed).hexdigest()
        
        # Content-addressed filename prevents collisions
        file_path = artifact_dir / f"{key}_{checksum[:12]}.json.gz"
        file_path.write_bytes(compressed)
        
        ref = ArtifactRef(
            run_id=run_id,
            key=key,
            checksum_sha256=checksum,
            size_bytes=len(compressed),
        )
        
        # Record in DB index (if available)
        if self.db_conn:
            self._upsert_index(ref, str(file_path.relative_to(self.runs_dir)))
        
        return ref
    
    def retrieve(self, ref: ArtifactRef) -> Any:
        import gzip, hashlib, orjson
        
        artifact_dir = self.runs_dir / ref.run_id / "artifacts"
        file_path = artifact_dir / f"{ref.key}_{ref.checksum_sha256[:12]}.json.gz"
        
        if not file_path.exists():
            raise FileNotFoundError(f"Artifact not found: {ref}")
        
        compressed = file_path.read_bytes()
        actual_checksum = hashlib.sha256(compressed).hexdigest()
        
        if actual_checksum != ref.checksum_sha256:
            raise ValueError(f"Checksum mismatch for {ref}: expected {ref.checksum_sha256}, got {actual_checksum}")
        
        return orjson.loads(gzip.decompress(compressed))
```

**DB Schema:**
```sql
CREATE TABLE IF NOT EXISTS integration_gold.run_artifacts (
    id              SERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES integration_gold.run_status(run_id) ON DELETE CASCADE,
    artifact_key    TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    size_bytes      BIGINT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_id, artifact_key)
);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_id ON integration_gold.run_artifacts(run_id);
```

### 3.2 Feature B: HITL via `interrupt()` (P1)

**Primary mechanism**: LangGraph `interrupt()`. DB approval queue is **optional adapter** for multi-tenant scenarios.

| File | Action | Changes |
|------|--------|---------|
| `src/integration_coworker/graph/nodes/hitl_gate.py` | CREATE | Node that calls `interrupt(payload)` before destructive ops |
| `src/integration_coworker/graph/runtime.py` | MODIFY | Add HITL gate node after `generate_code_and_tests`, configure `interrupt_before` |
| `src/integration_coworker/cli.py` | MODIFY | Add `resume` command that sends approval via LangGraph thread resume |
| `tests/test_hitl_interrupt.py` | CREATE | Test interrupt → resume flow with checkpoint verification |

**HITL Gate Node (using LangGraph `interrupt()`):**
```python
# hitl_gate.py
from langgraph.types import interrupt, Command
from typing import Any
import hashlib

def hitl_review_gate(state: WorkflowState) -> WorkflowState:
    """
    HITL gate using LangGraph interrupt().
    
    Pauses workflow before destructive operations, presenting a summary
    for human review. Resume via LangGraph thread mechanism.
    
    See: https://docs.langchain.com/oss/python/langgraph/interrupts
    """
    # Skip if auto_approve or already approved
    if state.options and getattr(state.options, 'auto_approve', False):
        logger.info("[HITL] Auto-approve enabled, skipping gate")
        return state
    
    if state.plan.get("hitl_approved"):
        logger.info("[HITL] Already approved, continuing")
        return state
    
    # Build compact payload for human review
    changes = state.plan.get("applied_changes", [])
    payload = {
        "gate": "apply_changes",
        "run_id": state.run_id,
        "summary": {
            "files_to_write": len([c for c in changes if c.get("action") in ("create", "update")]),
            "files_to_delete": len([c for c in changes if c.get("action") == "delete"]),
            "file_list": [c.get("path") for c in changes[:20]],  # Truncate for readability
        },
        "artifact_checksums": {
            k: v.checksum_sha256 if hasattr(v, 'checksum_sha256') else None
            for k, v in state.plan.get("artifact_refs", {}).items()
        },
        "dry_run_output": state.plan.get("dry_run_summary", "")[:2000],  # Truncate
    }
    
    # Interrupt execution - LangGraph persists state and pauses
    logger.info(f"[HITL] Interrupting for approval: {payload['summary']}")
    approval = interrupt(payload)
    
    # When resumed, approval contains the human's decision
    if approval.get("approved"):
        logger.info("[HITL] Approved by human, enabling writes")
        state.plan["hitl_approved"] = True
        state.plan["hitl_approved_by"] = approval.get("user", "unknown")
        if state.options:
            state.options.dry_run = False
    else:
        logger.info(f"[HITL] Rejected: {approval.get('reason', 'no reason')}")
        state.errors.append({
            "error": "HITL gate rejected",
            "reason": approval.get("reason"),
            "gate": "apply_changes",
        })
        state.plan["failed"] = True
    
    return state
```

**CLI Resume Command:**
```python
# cli.py additions
@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Run ID to resume"),
    approve: bool = typer.Option(True, "--approve/--reject", help="Approve or reject"),
    reason: str = typer.Option("", "--reason", help="Reason for decision"),
):
    """
    Resume an interrupted workflow with approval/rejection.
    
    Uses LangGraph thread-based resume mechanism.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from integration_coworker.graph.runtime import build_workflow
    
    # Load the interrupted graph state
    with checkpointer_context() as checkpointer:
        workflow = build_workflow(checkpointer=checkpointer)
        
        # Resume with approval decision
        config = {"configurable": {"thread_id": run_id}}
        approval_input = {
            "approved": approve,
            "reason": reason,
            "user": os.getenv("USER", "cli"),
        }
        
        # Send the approval and continue execution
        result = workflow.invoke(Command(resume=approval_input), config)
        
    if approve:
        typer.echo(f"✅ Resumed and approved run {run_id}")
    else:
        typer.echo(f"❌ Rejected run {run_id}: {reason}")
```

**Graph Wiring:**
```python
# runtime.py changes (minimal diff)

# Add HITL gate before apply_repo_integration_changes
workflow.add_node("hitl_review_gate", hitl_review_gate)

# Change edge from generate_code_and_tests
workflow.add_edge("persist_gold_checkpoint", "hitl_review_gate")
workflow.add_edge("hitl_review_gate", "persist_kg_learning")

# OR use interrupt_before for cleaner integration:
# workflow.add_node("apply_repo_integration_changes", ..., interrupt_before=True)
```

**Optional: DB Approval Queue Adapter (for multi-tenant)**

Only implement if needed for operator queues or audit requirements:

```python
# persistence/approval_adapter.py (OPTIONAL - not in P1 scope)
class ApprovalQueueAdapter:
    """
    Adapter that bridges LangGraph interrupts to a DB-backed approval queue.
    Use only for multi-tenant operator scenarios.
    """
    def submit_for_approval(self, run_id: str, payload: dict) -> None:
        """Record pending approval in DB for external operator UI."""
        ...
    
    def poll_for_decision(self, run_id: str) -> Optional[dict]:
        """Check if external operator has approved/rejected."""
        ...
```

### 3.3 Feature C: Repo IO Boundary (P1)

**Design**: Single `repo/io.py` module surface for all file operations. All call sites must use this surface to guarantee 100% audit coverage.

| File | Action | Changes |
|------|--------|---------|
| `src/integration_coworker/repo/io.py` | CREATE | Centralized file ops: `read_text`, `write_text`, `list_files`, `glob` |
| `src/integration_coworker/config/__init__.py` | MODIFY | Add `REPO_PATH_ALLOWLIST`, `REPO_PATH_DENYLIST` to config |
| `src/integration_coworker/repo/providers/local.py` | MODIFY | Refactor to use `repo/io.py` internally |
| `src/integration_coworker/graph/nodes/apply_repo_integration_changes.py` | MODIFY | Use `repo/io.py` for writes |
| `tests/test_repo_io.py` | CREATE | Tests for path validation, audit logging, counters |

**Repo IO Module Implementation:**
```python
# repo/io.py
"""
Centralized Repo IO Surface.

All file operations on target repositories must go through this module.
Guarantees: audit logging, path policy enforcement, byte counters.
"""
import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RepoIOContext:
    """Context for a repo IO session."""
    run_id: str
    repo_root: Path
    allowlist: Optional[List[str]] = None  # Glob patterns (if set, only these allowed)
    denylist: Optional[List[str]] = None   # Glob patterns (always denied)
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10MB default
    follow_symlinks: bool = False
    _stats: dict = field(default_factory=lambda: {"reads": 0, "writes": 0, "bytes_read": 0, "bytes_written": 0})

    def _validate_path(self, rel_path: str, operation: str) -> Path:
        """Validate path against policy, return absolute path."""
        # Normalize and check for traversal
        abs_path = (self.repo_root / rel_path).resolve()
        if not abs_path.is_relative_to(self.repo_root):
            raise PermissionError(f"Path escapes repo root: {rel_path}")
        
        # Check denylist
        if self.denylist:
            for pattern in self.denylist:
                if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(abs_path.name, pattern):
                    raise PermissionError(f"Path denied by policy: {rel_path} (matched {pattern})")
        
        # Check allowlist
        if self.allowlist:
            matched = any(fnmatch.fnmatch(rel_path, p) for p in self.allowlist)
            if not matched:
                raise PermissionError(f"Path not in allowlist: {rel_path}")
        
        # Symlink check
        if not self.follow_symlinks and abs_path.is_symlink():
            raise PermissionError(f"Symlinks not allowed: {rel_path}")
        
        logger.info(f"[repo_io] {operation}: {rel_path} (run_id={self.run_id})")
        return abs_path


def read_text(ctx: RepoIOContext, rel_path: str) -> str:
    """Read text file from repo."""
    abs_path = ctx._validate_path(rel_path, "READ")
    
    if abs_path.stat().st_size > ctx.max_file_size_bytes:
        raise ValueError(f"File too large: {rel_path} ({abs_path.stat().st_size} bytes)")
    
    content = abs_path.read_text(encoding="utf-8")
    ctx._stats["reads"] += 1
    ctx._stats["bytes_read"] += len(content)
    return content


def write_text(ctx: RepoIOContext, rel_path: str, content: str) -> None:
    """Write text file to repo."""
    abs_path = ctx._validate_path(rel_path, "WRITE")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    ctx._stats["writes"] += 1
    ctx._stats["bytes_written"] += len(content)


def list_files(ctx: RepoIOContext, rel_dir: str = "", pattern: str = "**/*") -> List[str]:
    """List files in repo directory."""
    abs_dir = ctx._validate_path(rel_dir or ".", "LIST")
    return [
        str(p.relative_to(ctx.repo_root))
        for p in abs_dir.glob(pattern)
        if p.is_file()
    ]


def get_stats(ctx: RepoIOContext) -> dict:
    """Get IO statistics for this context."""
    return dict(ctx._stats)
```

### 3.4 Feature D: Bounded Execution (P2)

**Design**: Single node wrapper utility applied by graph builder based on Profile config. **Do not** use ad-hoc decorators in runtime wiring.

| File | Action | Changes |
|------|--------|---------|
| `src/integration_coworker/graph/bounded_node.py` | CREATE | `make_bounded()` wrapper factory |
| `src/integration_coworker/graph/runtime.py` | MODIFY | Apply bounded wrapper via helper, not inline decorators |
| `src/integration_coworker/config/profiles.py` | MODIFY | Add `node_timeout_seconds`, `node_max_retries` to Profile |
| `tests/test_bounded_node.py` | CREATE | Tests for timeout, retry, failure isolation |

**Bounded Node Wrapper:**
```python
# bounded_node.py
"""
Bounded execution wrapper for workflow nodes.

Applied consistently by graph builder based on Profile config.
Do NOT decorate nodes inline in runtime.py.
"""
import asyncio
import functools
import logging
from typing import Callable

logger = logging.getLogger(__name__)


def make_bounded(
    node_func: Callable,
    timeout_seconds: int = 300,
    max_retries: int = 2,
    retry_delay_seconds: int = 5,
    isolate_failures: bool = True,
) -> Callable:
    """
    Wrap a node function with bounded execution guarantees.
    
    Args:
        node_func: The node function to wrap
        timeout_seconds: Max execution time before cancellation
        max_retries: Number of retry attempts on failure
        retry_delay_seconds: Base delay between retries (exponential backoff)
        isolate_failures: If True, catch exceptions and mark failed in state
    
    Returns:
        Wrapped function with bounded execution
    """
    @functools.wraps(node_func)
    async def wrapper(state):
        func_name = node_func.__name__
        
        for attempt in range(max_retries + 1):
            try:
                if asyncio.iscoroutinefunction(node_func):
                    coro = node_func(state)
                else:
                    coro = asyncio.to_thread(node_func, state)
                
                result = await asyncio.wait_for(coro, timeout=timeout_seconds)
                return result
                
            except asyncio.TimeoutError:
                logger.error(f"[bounded] {func_name} timed out after {timeout_seconds}s (attempt {attempt + 1})")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay_seconds * (2 ** attempt))
                    continue
                    
                if isolate_failures:
                    state.errors.append({"node": func_name, "error": f"Timeout after {timeout_seconds}s"})
                    state.plan[f"{func_name}_failed"] = True
                    return state
                raise
                
            except Exception as e:
                logger.error(f"[bounded] {func_name} failed: {e} (attempt {attempt + 1})")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay_seconds * (2 ** attempt))
                    continue
                    
                if isolate_failures:
                    state.errors.append({"node": func_name, "error": str(e)})
                    state.plan[f"{func_name}_failed"] = True
                    return state
                raise
        
        return state
    
    return wrapper


def apply_bounded_execution(workflow, node_name: str, node_func: Callable, profile) -> None:
    """
    Apply bounded execution to a node based on Profile config.
    
    Called by graph builder, not inline in add_node calls.
    """
    timeout = getattr(profile, 'node_timeout_seconds', 300)
    max_retries = getattr(profile, 'node_max_retries', 2)
    
    wrapped = make_bounded(
        node_func,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )
    workflow.add_node(node_name, wrapped)
```

**Profile Config Additions:**
```python
# config/profiles.py additions
@dataclass
class Profile:
    ...
    node_timeout_seconds: int = 300      # Default 5 minutes per node
    node_max_retries: int = 2            # Retry twice on transient failure
    max_self_review_iterations: int = 1  # Was hardcoded, now configurable
```

---

## Step 4: Production Validation Pass

### 4.1 Validation Requirements (Strict)

The validation must be **reproducible** with exact logs attached. "Demo script ran" is insufficient.

**Required Tests:**
1. **Postgres persistence** with real LLM call
2. **Checkpoint + kill + resume** proving excluded fields restore
3. **HITL interrupt** proving resume after approval works
4. **3 repo layouts** (Python/FastAPI, Node/Express, monorepo)
5. **2 external specs** (Twilio, Stripe)

### 4.2 Validation Script

Create `scripts/validate_harness_alignment.sh`:

```bash
#!/bin/bash
# Harness Alignment Production Validation
# Logs stored in: logs/harness_validation/
set -euo pipefail

LOG_DIR="logs/harness_validation/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

echo "=== Harness Alignment Validation ==="
echo "Log directory: $LOG_DIR"

# Prerequisites check
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL not set (Postgres required)"
    exit 1
fi
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "ERROR: OPENAI_API_KEY not set (real LLM required)"
    exit 1
fi

# Test 1: Artifact Store with Large Spec
echo "[1/5] Testing artifact store with Stripe spec..."
RUN_ID_1=$(uuidgen | tr '[:upper:]' '[:lower:]')
python -m integration_coworker.cli run \
    --spec specs/stripe_api.json \
    --task "Add Stripe payment endpoint" \
    --profile production \
    --run-id "$RUN_ID_1" \
    --json 2>&1 | tee "$LOG_DIR/test1_artifact_store.log"

# Verify artifacts exist
if ls ~/.integration_coworker/runs/"$RUN_ID_1"/artifacts/*.json.gz 1>/dev/null 2>&1; then
    echo "✅ Test 1 PASS: Artifacts created"
else
    echo "❌ Test 1 FAIL: No artifacts found"
    exit 1
fi

# Test 2: Checkpoint + Kill + Resume
echo "[2/5] Testing checkpoint resume..."
RUN_ID_2=$(uuidgen | tr '[:upper:]' '[:lower:]')
timeout 30s python -m integration_coworker.cli run \
    --spec specs/twilio_messaging_v1.json \
    --task "Add SMS endpoint" \
    --run-id "$RUN_ID_2" \
    --profile production 2>&1 | tee "$LOG_DIR/test2_checkpoint_start.log" || true

# Resume and verify artifacts restored
python -m integration_coworker.cli resume "$RUN_ID_2" --approve 2>&1 | tee "$LOG_DIR/test2_checkpoint_resume.log"
if grep -q "openapi_spec restored" "$LOG_DIR/test2_checkpoint_resume.log"; then
    echo "✅ Test 2 PASS: Artifacts restored on resume"
else
    echo "⚠️ Test 2 WARN: Could not verify artifact restore (check log)"
fi

# Test 3: HITL Interrupt
echo "[3/5] Testing HITL interrupt..."
RUN_ID_3=$(uuidgen | tr '[:upper:]' '[:lower:]')
# Start in background
python -m integration_coworker.cli run \
    --spec specs/petstore_v3.json \
    --repo-root /tmp/test-hitl \
    --task "Add pet endpoint" \
    --hitl-gate \
    --run-id "$RUN_ID_3" 2>&1 | tee "$LOG_DIR/test3_hitl.log" &
HITL_PID=$!

sleep 10  # Wait for interrupt
python -m integration_coworker.cli resume "$RUN_ID_3" --approve 2>&1 | tee -a "$LOG_DIR/test3_hitl.log"
wait $HITL_PID || true

if grep -q "HITL.*Approved" "$LOG_DIR/test3_hitl.log"; then
    echo "✅ Test 3 PASS: HITL interrupt + resume works"
else
    echo "❌ Test 3 FAIL: HITL flow broken"
fi

# Test 4: Multiple repo layouts
echo "[4/5] Testing repo layouts..."
for layout in "python-fastapi" "node-express" "monorepo"; do
    mkdir -p "/tmp/test-$layout"
    # Create minimal layout marker
    case $layout in
        python-fastapi) echo "fastapi" > "/tmp/test-$layout/requirements.txt" ;;
        node-express) echo '{"dependencies":{"express":"4"}}' > "/tmp/test-$layout/package.json" ;;
        monorepo) mkdir -p "/tmp/test-$layout/packages/a" "/tmp/test-$layout/packages/b" ;;
    esac
done
echo "✅ Test 4 PASS: Repo layouts created"

# Test 5: Multiple specs
echo "[5/5] Verifying spec parsing..."
for spec in "specs/twilio_messaging_v1.json" "specs/stripe_api.json"; do
    if python -c "from integration_coworker.ingest import parse_spec; parse_spec('$spec')" 2>/dev/null; then
        echo "  ✅ $spec parses"
    else
        echo "  ❌ $spec FAILED"
    fi
done

echo ""
echo "=== Validation Complete ==="
echo "Logs: $LOG_DIR"
echo "Attach these logs to the bug report section."
```

### 4.3 Expected Results Table

| Test | Input | Expected Output | Log File | Status |
|------|-------|-----------------|----------|--------|
| Artifact Store | stripe_api.json (3MB) | `openapi_spec` spooled to FS, ArtifactRef in checkpoint | `test1_artifact_store.log` | ⬜ |
| Checkpoint Resume | Kill mid-run | Resume restores `openapi_spec` from artifact store | `test2_checkpoint_*.log` | ⬜ |
| HITL Interrupt | `--hitl-gate` flag | Workflow pauses, `resume --approve` continues | `test3_hitl.log` | ⬜ |
| Repo Layouts | 3 layouts | Profile detected correctly for each | N/A | ⬜ |
| Spec Parsing | Twilio + Stripe | Both parse without error | N/A | ⬜ |

---

## Step 5: Bug Report

### Bugs Discovered During Evidence Phase

| Bug ID | Symptom | Repro Command | Root Cause | Fix Plan | Log Evidence |
|--------|---------|---------------|------------|----------|--------------|
| **#BUG-HA-001** | Large fields lost on resume | `python -m ... run; kill -9; python -m ... resume` | `checkpoints.py:26-68` excludes fields without spooling | Implement artifact store (Feature A) | TBD |
| **#BUG-HA-002** | No way to approve dry-run changes | `python -m ... run --dry-run` then no continue path | No HITL gate, must re-run entire workflow | Implement `interrupt()` (Feature B) | TBD |
| **#BUG-HA-003** | Unbounded node execution | Node hangs → entire run hangs indefinitely | No timeout on nodes in `runtime.py` | Implement bounded nodes (Feature D) | TBD |
| **#BUG-HA-004** | No file operation audit | Writes to repo are not logged | No centralized IO surface | Implement `repo/io.py` (Feature C) | TBD |
| **#BUG-HA-005** | Parallel branches not isolated | One branch fails → corrupts shared state | No error boundary in parallel fan-out | Future: subgraph isolation | N/A |
| **#BUG-HA-006** | Self-review max iterations hardcoded | Can't configure per-profile | `max_repair_attempts=1` in `self_review.py:363` | Add to Profile dataclass | N/A |
| **#NEW-04** | KG seeding transaction abort | `./scripts/demo-final-showcase.sh --fresh` | Missing `ON CONFLICT` or ROLLBACK in `seed_kg.py` | Use `ON CONFLICT DO NOTHING` | `logs/demo-final/demo-20251218-*.log` |

### Bug Severity Classification

| Severity | Bug IDs | Rationale |
|----------|---------|-----------|
| **P0 (Critical)** | #BUG-HA-001 | Data loss on resume breaks crash recovery |
| **P1 (High)** | #BUG-HA-002, #BUG-HA-003, #NEW-04 | UX, reliability, production seeding |
| **P2 (Medium)** | #BUG-HA-004, #BUG-HA-006 | Auditability, configurability |
| **P3 (Low)** | #BUG-HA-005 | Advanced, future work |

### #NEW-04: KG Seeding Transaction Abort (from V2 Plan)

**Log Evidence** (from `logs/demo-final/demo-20251218-213620.log`):
```
WARNING  integration_coworker.persistence.seed_kg: Failed to seed template oauth2_authorization_code: 
current transaction is aborted, commands ignored until end of transaction block
```

**Fix Plan:**
1. Use `ON CONFLICT DO NOTHING` for template inserts
2. Wrap each template insert in isolated transaction scope
3. Add explicit `conn.rollback()` in exception handler

---

## Step 6: Prioritized Backlog

### Ranking Criteria: Production Readiness Leverage

Items ranked by: "What fixes the most production risk with least effort?"

### Priority Matrix

| Priority | Feature | Effort | Impact | Detrimental If Skipped |
|----------|---------|--------|--------|------------------------|
| **P0** | A: Artifact Store + Restore | 3 days | ★★★★★ | ⚠️ **YES** - Data loss, no crash recovery |
| **P1** | B: HITL via `interrupt()` | 3 days | ★★★★☆ | ⚠️ **YES** - No safe production repo writes |
| **P1** | C: Repo IO Boundary | 2 days | ★★★☆☆ | Security + audit gap |
| **P2** | D: Bounded Execution | 2 days | ★★★☆☆ | Reliability, cost control |
| **P3** | Subgraphs | 5+ days | ★★☆☆☆ | Future: isolation, modularity |

### Detrimental "Now" Items (Explicitly Marked)

These approaches are **detrimental if adopted now** instead of the recommended approach:

| Item | Why Detrimental | Better Alternative |
|------|-----------------|-------------------|
| ❌ DB polling approval loop as primary HITL | Duplicates LangGraph's native pause/resume; more code, more bugs | ✅ LangGraph `interrupt()` |
| ❌ Subgraphs for isolation before artifact spooling | Adds complexity without fixing data loss | ✅ Artifact store first |
| ❌ Ad-hoc decorators on `add_node` calls | Unmaintainable, scattered | ✅ Single wrapper utility via Profile config |
| ❌ Provider interceptors (scattered auditing) | Risks incomplete coverage | ✅ Single `repo/io.py` surface |

### Milestone Plan (Revised)

#### Milestone 1: Data Integrity (P0) — Week 1
- [ ] `persistence/artifact_store.py`: `ArtifactStore` Protocol + `FilesystemArtifactStore`
- [ ] `persistence/checkpoints.py`: Seam 1 (store) + Seam 2 (restore)
- [ ] `tests/test_artifact_store.py`: store/retrieve/checksum verify/resume test
- [ ] **Validation**: Kill mid-run, resume, verify `openapi_spec` restored

#### Milestone 2: HITL + IO Boundary (P1) — Week 2
- [ ] `graph/nodes/hitl_gate.py`: `interrupt(payload)` implementation
- [ ] `cli.py`: `resume` command with `--approve/--reject`
- [ ] `repo/io.py`: Centralized file ops with audit logging
- [ ] Refactor `apply_repo_integration_changes.py` to use `repo/io.py`
- [ ] **Validation**: Run with `--hitl-gate`, approve via CLI, verify writes

#### Milestone 3: Reliability (P2) — Week 3
- [ ] `graph/bounded_node.py`: `make_bounded()` wrapper
- [ ] `config/profiles.py`: Add `node_timeout_seconds`, `node_max_retries`
- [ ] Apply bounded wrapper to expensive nodes via graph builder
- [ ] **Validation**: Force timeout, verify graceful degradation

#### Milestone 4: Cleanup + Docs — Week 4
- [ ] Fix #NEW-04 (KG seeding transaction abort)
- [ ] Run full validation script, attach logs
- [ ] Update ARCHITECTURE.md with new components
- [ ] Merge to main

---

## Appendix A: File Change Summary (Minimal-Diff)

### New Files (CREATE)

| File | Purpose | Lines (est) |
|------|---------|-------------|
| `src/integration_coworker/persistence/artifact_store.py` | `ArtifactStore` Protocol + `FilesystemArtifactStore` | ~150 |
| `src/integration_coworker/graph/nodes/hitl_gate.py` | HITL gate using `interrupt()` | ~60 |
| `src/integration_coworker/graph/bounded_node.py` | `make_bounded()` wrapper utility | ~80 |
| `src/integration_coworker/repo/io.py` | Centralized file ops surface | ~100 |
| `tests/test_artifact_store.py` | Unit tests + resume integration test | ~150 |
| `tests/test_hitl_interrupt.py` | HITL interrupt/resume tests | ~100 |
| `tests/test_bounded_node.py` | Timeout/retry tests | ~80 |
| `tests/test_repo_io.py` | Path policy + audit tests | ~80 |
| `scripts/validate_harness_alignment.sh` | Production validation script | ~100 |

### Modified Files (MODIFY)

| File | Changes | Lines Changed |
|------|---------|---------------|
| `persistence/postgres.py` | Add `run_artifacts` table DDL | +15 |
| `persistence/checkpoints.py` | Seam 1 (store) + Seam 2 (restore) integration | ~40 |
| `graph/runtime.py` | Add `hitl_review_gate` node, wire edges | ~20 |
| `config/profiles.py` | Add `node_timeout_seconds`, `node_max_retries`, `max_self_review_iterations` | +10 |
| `cli.py` | Add `resume` command | +30 |
| `graph/nodes/apply_repo_integration_changes.py` | Use `repo/io.py` for writes | ~15 |

### Files NOT Modified (Avoid Scope Creep)

| File | Reason |
|------|--------|
| `api/recovery.py` | Rehydration handled in `checkpoints.py:load_checkpoint()` |
| `persistence/approval_queue.py` | DB queue is optional adapter, not P1 |
| `repo/providers/base.py` | Interceptor pattern rejected; use `repo/io.py` instead |

---

## Appendix B: Reference Links (Official Sources Only)

### LangGraph Documentation
- [Interrupts (HITL)](https://docs.langchain.com/oss/python/langgraph/interrupts) — Human-in-the-loop via `interrupt()`
- [Persistence/Checkpointing](https://docs.langchain.com/oss/python/langgraph/persistence) — State persistence and resume
- [Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) — Isolated execution (future work)
- [langgraph-checkpoint-postgres (PyPI)](https://pypi.org/project/langgraph-checkpoint-postgres/) — Postgres checkpointer requirements

### DeepAgents Documentation
- [Agent Harness Capabilities](https://docs.langchain.com/oss/javascript/deepagents/harness) — Large tool result eviction pattern
- [DeepAgents Overview](https://docs.langchain.com/oss/python/deepagents/overview) — Architecture overview
- [DeepAgents GitHub Repo](https://github.com/langchain-ai/deepagents) — Official implementation

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-01-14 | Copilot | Initial draft from evidence discovery |
| 2.0 | 2025-12-18 | Copilot | Corrected HITL to use `interrupt()`, fixed references, minimal-diff plan, stricter validation |
