# V3 Streaming Persistence + Lazy Loading Implementation Plan

> **Purpose**: Detailed implementation plan to reduce memory footprint of `WorkflowState` by streaming large data to the database immediately and loading it lazily when needed.
>
> **Author**: Copilot Architecture Analysis
> **Date**: December 5, 2025
> **Status**: Draft

---

## 1. Executive Summary

### 1.1 Problem Statement

The current `WorkflowState` carries all data in memory throughout the entire workflow:

| Field | Typical Size | Large Spec Size | Carried Until |
|-------|--------------|-----------------|---------------|
| `openapi_spec` | 500KB | 10-50MB | End of run |
| `doc_chunks` | 2MB | 40MB+ | End of run |
| `spec_chunk_embeddings` | 6MB | 60MB+ | End of run |
| `spec_documents[].content` | 500KB | 10-50MB | End of run |

For AWS-scale specs (10,000+ endpoints), this results in **200MB+ RAM per run** and potential OOM crashes.

### 1.2 Solution: Streaming Persistence + Lazy Loading

**Streaming Persistence**: Write large data to the database immediately after creation, then clear from memory.

**Lazy Loading**: When downstream nodes need the data, load only what's required from the database.

### 1.3 Key Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Memory per run (large spec) | 200MB+ | <20MB |
| Max spec size | ~5,000 endpoints | Unlimited |
| DB writes | 3 batches | Streaming |
| DB reads | Minimal | More (lazy) |

---

## 2. Architecture Overview

### 2.1 Current Flow (In-Memory)

```
ingest_spec ──────────────────────────────────────────────────────────────────┐
    │ state.doc_chunks = [...] (HELD IN MEMORY)                               │
    │ state.spec_documents[].content = "..." (HELD IN MEMORY)                 │
    ▼                                                                         │
detect_and_parse_spec                                                         │
    │ state.openapi_spec = {...} (HELD IN MEMORY)                             │
    ▼                                                                         │
build_silver_api_model                                                        │
    │ state.endpoints, state.schemas... (small, OK)                           │
    ▼                                                                         │
embed_spec_chunks                                                             │
    │ state.spec_chunk_embeddings = [...] (HELD IN MEMORY)                    │
    ▼                                                                         │
persist_silver_checkpoint ◄─────────────────────────────────────────────────┘
    │ FINALLY writes all to DB
    ▼
(downstream nodes)
```

### 2.2 Proposed Flow (Streaming + Lazy)

```
ingest_spec ──────────────────────────────────────────────────────────────────┐
    │ ┌───────────────────────────────────────────────────────────────────┐   │
    │ │ STREAM: Write raw content to spec_bronze.raw_specs                │   │
    │ │ STREAM: Write chunks to spec_silver.spec_chunks (content only)    │   │
    │ │ CLEAR: state.doc_chunks = []                                      │   │
    │ │ KEEP: state.spec_document_ids = [id1, id2, ...]                   │   │
    │ └───────────────────────────────────────────────────────────────────┘   │
    ▼                                                                         │
detect_and_parse_spec                                                         │
    │ ┌───────────────────────────────────────────────────────────────────┐   │
    │ │ PARSE: Load from raw_specs, parse, keep only structure            │   │
    │ │ CLEAR: Don't store full openapi_spec in state                     │   │
    │ │ KEEP: state.parsed_spec_metadata = {...}                          │   │
    │ └───────────────────────────────────────────────────────────────────┘   │
    ▼                                                                         │
build_silver_api_model                                                        │
    │ Already works on parsed structure (small)                               │
    ▼                                                                         │
embed_spec_chunks                                                             │
    │ ┌───────────────────────────────────────────────────────────────────┐   │
    │ │ LAZY LOAD: Get chunk content from DB in batches                   │   │
    │ │ STREAM: Update spec_chunks.embedding as computed                  │   │
    │ │ CLEAR: Don't store embeddings in state                            │   │
    │ │ KEEP: state.embedding_count = N                                   │   │
    │ └───────────────────────────────────────────────────────────────────┘   │
    ▼                                                                         │
persist_silver_checkpoint                                                     │
    │ ┌───────────────────────────────────────────────────────────────────┐   │
    │ │ Chunks already in DB - just persist metadata (endpoints, etc.)    │   │
    │ │ Much faster - no chunk INSERT                                     │   │
    │ └───────────────────────────────────────────────────────────────────┘   │
    ▼                                                                         │
(downstream nodes)                                                            │
    │ ┌───────────────────────────────────────────────────────────────────┐   │
    │ │ LAZY LOAD: Use semantic_search() to get relevant chunks           │   │
    │ │ Never load all chunks into memory                                 │   │
    │ └───────────────────────────────────────────────────────────────────┘   │
```

---

## 3. Files Requiring Changes

### 3.1 Change Matrix

| File | Change Type | Risk | Priority |
|------|-------------|------|----------|
| `graph/state.py` | Modify | Medium | P0 |
| `graph/nodes/ingest_spec.py` | Major refactor | High | P0 |
| `graph/nodes/detect_and_parse_spec.py` | Modify | Medium | P1 |
| `graph/nodes/embed_spec_chunks.py` | Major refactor | High | P0 |
| `graph/nodes/persist_silver_checkpoint.py` | Modify | Medium | P1 |
| `graph/nodes/build_report.py` | Minor | Low | P2 |
| `graph/nodes/persist_run_outcome.py` | Minor | Low | P2 |
| `persistence/streaming.py` | **NEW FILE** | Medium | P0 |
| `persistence/lazy_loader.py` | **NEW FILE** | Medium | P0 |
| `persistence/db.py` | Minor additions | Low | P1 |
| `persistence/postgres.py` | Minor additions | Low | P1 |
| `retrieval/semantic_search.py` | Minor | Low | P2 |
| `tests/conftest.py` | Add fixtures | Low | P1 |

### 3.2 Dependency Order

```
Phase 1: Foundation
├── persistence/streaming.py (NEW)
├── persistence/lazy_loader.py (NEW)
└── persistence/db.py (add helper functions)

Phase 2: State Slimming
├── graph/state.py (remove large fields, add ID fields)
└── tests/conftest.py (update fixtures)

Phase 3: Node Refactoring
├── graph/nodes/ingest_spec.py
├── graph/nodes/embed_spec_chunks.py
├── graph/nodes/detect_and_parse_spec.py
└── graph/nodes/persist_silver_checkpoint.py

Phase 4: Downstream Updates
├── graph/nodes/build_report.py
├── graph/nodes/persist_run_outcome.py
└── retrieval/semantic_search.py
```

---

## 4. Detailed Implementation Specifications

### 4.1 NEW: `persistence/streaming.py`

**Purpose**: Stream large data to database immediately, bypassing WorkflowState accumulation.

```python
"""
Streaming persistence for large data.

Writes chunks and embeddings to DB immediately without accumulating in memory.
All functions are transaction-safe and idempotent.
"""
from typing import Iterator, List, Optional, Tuple
import logging
import hashlib

from integration_coworker.persistence.db import get_connection, get_engine_type
from integration_coworker.persistence.sql_helpers import get_engine_type

logger = logging.getLogger(__name__)

# Batch sizes for streaming operations
CHUNK_BATCH_SIZE = 100  # Write chunks in batches of 100
EMBEDDING_BATCH_SIZE = 50  # Update embeddings in batches of 50


def stream_raw_spec_to_bronze(
    content: bytes,
    uri: str,
    content_type: str,
    source_system_id: Optional[int] = None,
) -> int:
    """
    Stream raw spec content to spec_bronze.raw_specs immediately.
    
    Returns: raw_spec_id
    
    Idempotent: Uses SHA256 hash for deduplication.
    """
    sha256 = hashlib.sha256(content).hexdigest()
    
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            # Check if already exists
            cur.execute("""
                SELECT id FROM spec_bronze.raw_specs 
                WHERE sha256 = %s
            """, (sha256,))
            row = cur.fetchone()
            if row:
                return row[0]
            
            # Insert new
            cur.execute("""
                INSERT INTO spec_bronze.raw_specs 
                    (source_system_id, uri, sha256, content_type, raw_content)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (source_system_id, uri, sha256, content_type, content))
            raw_spec_id = cur.fetchone()[0]
        else:
            # SQLite
            cur.execute("""
                SELECT id FROM raw_specs WHERE sha256 = ?
            """, (sha256,))
            row = cur.fetchone()
            if row:
                return row[0]
            
            cur.execute("""
                INSERT INTO raw_specs 
                    (source_system_id, uri, sha256, content_type, raw_content)
                VALUES (?, ?, ?, ?, ?)
            """, (source_system_id, uri, sha256, content_type, content))
            raw_spec_id = cur.lastrowid
        
        conn.commit()
        logger.debug(f"Streamed raw spec to bronze: {uri} -> id={raw_spec_id}")
        return raw_spec_id
        
    finally:
        conn.close()


def stream_chunks_to_silver(
    chunks: Iterator[Tuple[int, str]],  # (chunk_index, content)
    spec_document_id: int,
    batch_size: int = CHUNK_BATCH_SIZE,
) -> int:
    """
    Stream chunks to spec_silver.spec_chunks in batches.
    
    Args:
        chunks: Iterator of (chunk_index, content) tuples
        spec_document_id: Foreign key to spec_documents
        batch_size: Number of chunks per batch write
        
    Returns: Total number of chunks written
    
    Note: Embeddings are NULL initially; updated by stream_embeddings_to_chunks().
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    total_written = 0
    batch = []
    
    try:
        for chunk_index, content in chunks:
            batch.append((spec_document_id, chunk_index, content))
            
            if len(batch) >= batch_size:
                _write_chunk_batch(cur, batch, engine)
                total_written += len(batch)
                batch = []
                
                if total_written % 500 == 0:
                    logger.debug(f"Streamed {total_written} chunks...")
        
        # Write remaining batch
        if batch:
            _write_chunk_batch(cur, batch, engine)
            total_written += len(batch)
        
        conn.commit()
        logger.info(f"Streamed {total_written} chunks to silver for spec_document_id={spec_document_id}")
        return total_written
        
    finally:
        conn.close()


def _write_chunk_batch(
    cur,
    batch: List[Tuple[int, int, str]],  # (spec_document_id, chunk_index, content)
    engine: str,
) -> None:
    """Write a batch of chunks. Internal helper."""
    if engine == "postgres":
        from psycopg2.extras import execute_values
        execute_values(
            cur,
            """
            INSERT INTO spec_silver.spec_chunks (spec_document_id, chunk_index, content)
            VALUES %s
            ON CONFLICT (spec_document_id, chunk_index) DO UPDATE SET content = EXCLUDED.content
            """,
            batch,
        )
    else:
        cur.executemany(
            """
            INSERT OR REPLACE INTO spec_chunks (spec_document_id, chunk_index, content)
            VALUES (?, ?, ?)
            """,
            batch,
        )


def stream_embeddings_to_chunks(
    embeddings: Iterator[Tuple[int, List[float]]],  # (chunk_id, embedding)
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> int:
    """
    Stream embeddings to existing spec_chunks rows.
    
    Args:
        embeddings: Iterator of (chunk_id, embedding_vector) tuples
        batch_size: Number of updates per batch
        
    Returns: Total number of embeddings written
    
    Note: Chunks must already exist (created by stream_chunks_to_silver).
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    total_written = 0
    batch = []
    
    try:
        for chunk_id, embedding in embeddings:
            batch.append((chunk_id, embedding))
            
            if len(batch) >= batch_size:
                _write_embedding_batch(cur, batch, engine)
                total_written += len(batch)
                batch = []
        
        # Write remaining batch
        if batch:
            _write_embedding_batch(cur, batch, engine)
            total_written += len(batch)
        
        conn.commit()
        logger.info(f"Streamed {total_written} embeddings to chunks")
        return total_written
        
    finally:
        conn.close()


def _write_embedding_batch(
    cur,
    batch: List[Tuple[int, List[float]]],  # (chunk_id, embedding)
    engine: str,
) -> None:
    """Write a batch of embeddings. Internal helper."""
    import json
    
    if engine == "postgres":
        # Use pgvector native format
        for chunk_id, embedding in batch:
            vector_literal = "[" + ",".join(str(x) for x in embedding) + "]"
            cur.execute(
                """
                UPDATE spec_silver.spec_chunks 
                SET embedding = %s::vector
                WHERE id = %s
                """,
                (vector_literal, chunk_id),
            )
    else:
        # SQLite: store as JSON
        for chunk_id, embedding in batch:
            cur.execute(
                """
                UPDATE spec_chunks SET embedding = ? WHERE id = ?
                """,
                (json.dumps(embedding), chunk_id),
            )


def get_chunk_ids_for_spec(spec_document_id: int) -> List[int]:
    """
    Get all chunk IDs for a spec document.
    
    Used by embed_spec_chunks to know which chunks to embed.
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT id FROM spec_silver.spec_chunks
                WHERE spec_document_id = %s
                ORDER BY chunk_index
            """, (spec_document_id,))
        else:
            cur.execute("""
                SELECT id FROM spec_chunks
                WHERE spec_document_id = ?
                ORDER BY chunk_index
            """, (spec_document_id,))
        
        return [row[0] for row in cur.fetchall()]
        
    finally:
        conn.close()
```

### 4.2 NEW: `persistence/lazy_loader.py`

**Purpose**: Load data from database on demand, avoiding full materialization.

```python
"""
Lazy loading utilities for streaming persistence.

Provides generators and paginated access to large datasets without
loading everything into memory.
"""
from typing import Iterator, List, Optional, Tuple
import json
import logging

from integration_coworker.persistence.db import get_connection, get_engine_type

logger = logging.getLogger(__name__)

# Default page sizes
DEFAULT_PAGE_SIZE = 100


def load_raw_spec_content(raw_spec_id: int) -> bytes:
    """
    Load raw spec content from Bronze layer.
    
    Use sparingly - returns full content.
    Prefer load_spec_content_stream() for large specs.
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT raw_content FROM spec_bronze.raw_specs WHERE id = %s
            """, (raw_spec_id,))
        else:
            cur.execute("""
                SELECT raw_content FROM raw_specs WHERE id = ?
            """, (raw_spec_id,))
        
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Raw spec not found: {raw_spec_id}")
        
        return row[0]
        
    finally:
        conn.close()


def iter_chunks(
    spec_document_id: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    include_embedding: bool = False,
) -> Iterator[Tuple[int, int, str, Optional[List[float]]]]:
    """
    Iterate over chunks for a spec document with pagination.
    
    Yields: (chunk_id, chunk_index, content, embedding or None)
    
    Memory efficient - only loads page_size chunks at a time.
    """
    engine = get_engine_type()
    offset = 0
    
    while True:
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            if engine == "postgres":
                if include_embedding:
                    cur.execute("""
                        SELECT id, chunk_index, content, embedding::text
                        FROM spec_silver.spec_chunks
                        WHERE spec_document_id = %s
                        ORDER BY chunk_index
                        LIMIT %s OFFSET %s
                    """, (spec_document_id, page_size, offset))
                else:
                    cur.execute("""
                        SELECT id, chunk_index, content, NULL
                        FROM spec_silver.spec_chunks
                        WHERE spec_document_id = %s
                        ORDER BY chunk_index
                        LIMIT %s OFFSET %s
                    """, (spec_document_id, page_size, offset))
            else:
                cur.execute("""
                    SELECT id, chunk_index, content, embedding
                    FROM spec_chunks
                    WHERE spec_document_id = ?
                    ORDER BY chunk_index
                    LIMIT ? OFFSET ?
                """, (spec_document_id, page_size, offset))
            
            rows = cur.fetchall()
            if not rows:
                return
            
            for row in rows:
                chunk_id, chunk_index, content, embedding_raw = row
                
                # Parse embedding if present
                embedding = None
                if embedding_raw:
                    if isinstance(embedding_raw, str):
                        # Postgres vector literal or SQLite JSON
                        if embedding_raw.startswith("["):
                            embedding = json.loads(embedding_raw)
                        else:
                            # Postgres vector format: [0.1,0.2,...]
                            embedding = [float(x) for x in embedding_raw.strip("[]").split(",")]
                    elif isinstance(embedding_raw, list):
                        embedding = embedding_raw
                
                yield (chunk_id, chunk_index, content, embedding)
            
            offset += page_size
            
        finally:
            conn.close()


def get_chunk_by_id(chunk_id: int) -> Tuple[int, str, Optional[List[float]]]:
    """
    Load a single chunk by ID.
    
    Returns: (chunk_index, content, embedding or None)
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT chunk_index, content, embedding::text
                FROM spec_silver.spec_chunks
                WHERE id = %s
            """, (chunk_id,))
        else:
            cur.execute("""
                SELECT chunk_index, content, embedding
                FROM spec_chunks
                WHERE id = ?
            """, (chunk_id,))
        
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Chunk not found: {chunk_id}")
        
        chunk_index, content, embedding_raw = row
        
        embedding = None
        if embedding_raw:
            if isinstance(embedding_raw, str):
                if embedding_raw.startswith("["):
                    embedding = json.loads(embedding_raw)
                else:
                    embedding = [float(x) for x in embedding_raw.strip("[]").split(",")]
        
        return (chunk_index, content, embedding)
        
    finally:
        conn.close()


def get_chunk_count(spec_document_id: int) -> int:
    """
    Get total number of chunks for a spec document.
    
    Cheap metadata query - no content loaded.
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT COUNT(*) FROM spec_silver.spec_chunks
                WHERE spec_document_id = %s
            """, (spec_document_id,))
        else:
            cur.execute("""
                SELECT COUNT(*) FROM spec_chunks
                WHERE spec_document_id = ?
            """, (spec_document_id,))
        
        return cur.fetchone()[0]
        
    finally:
        conn.close()


def get_embedding_count(spec_document_id: int) -> int:
    """
    Get count of chunks that have embeddings.
    
    Cheap metadata query - no content loaded.
    """
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                SELECT COUNT(*) FROM spec_silver.spec_chunks
                WHERE spec_document_id = %s AND embedding IS NOT NULL
            """, (spec_document_id,))
        else:
            cur.execute("""
                SELECT COUNT(*) FROM spec_chunks
                WHERE spec_document_id = ? AND embedding IS NOT NULL
            """, (spec_document_id,))
        
        return cur.fetchone()[0]
        
    finally:
        conn.close()


def iter_chunks_for_embedding(
    spec_document_id: int,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Iterator[Tuple[int, str]]:
    """
    Iterate over chunks that need embedding (embedding IS NULL).
    
    Yields: (chunk_id, content)
    
    Used by embed_spec_chunks to process only un-embedded chunks.
    """
    engine = get_engine_type()
    offset = 0
    
    while True:
        conn = get_connection()
        cur = conn.cursor()
        
        try:
            if engine == "postgres":
                cur.execute("""
                    SELECT id, content
                    FROM spec_silver.spec_chunks
                    WHERE spec_document_id = %s AND embedding IS NULL
                    ORDER BY chunk_index
                    LIMIT %s OFFSET %s
                """, (spec_document_id, page_size, offset))
            else:
                cur.execute("""
                    SELECT id, content
                    FROM spec_chunks
                    WHERE spec_document_id = ? AND embedding IS NULL
                    ORDER BY chunk_index
                    LIMIT ? OFFSET ?
                """, (spec_document_id, page_size, offset))
            
            rows = cur.fetchall()
            if not rows:
                return
            
            for chunk_id, content in rows:
                yield (chunk_id, content)
            
            offset += page_size
            
        finally:
            conn.close()
```

### 4.3 MODIFY: `graph/state.py`

**Changes**:
1. Remove `doc_chunks` and `spec_chunk_embeddings` (large fields)
2. Add `spec_document_ids` and metadata fields
3. Add helper properties for backward compatibility

```python
# BEFORE (current)
@dataclass
class WorkflowState:
    # ...
    doc_chunks: List[str] = field(default_factory=list)  # REMOVE
    spec_chunk_embeddings: List[SpecChunkEmbedding] = field(default_factory=list)  # REMOVE
    openapi_spec: Optional[Dict[str, Any]] = None  # REMOVE (keep parsed metadata only)
    # ...


# AFTER (proposed)
@dataclass
class WorkflowState:
    # Inputs
    source_refs: List[str]
    spec_refs: List[str]
    task_description: str
    provider_code: Optional[str] = None
    options: Optional[IntegrationOptions] = None

    # IDs pointing to DB (replaces large in-memory fields)
    spec_document_ids: List[int] = field(default_factory=list)  # NEW
    raw_spec_ids: List[int] = field(default_factory=list)  # NEW
    source_system_id: Optional[int] = None  # NEW
    
    # Metadata (replaces large fields with counts)
    chunk_count: int = 0  # NEW - replaces len(doc_chunks)
    embedding_count: int = 0  # NEW - replaces len(spec_chunk_embeddings)
    
    # Bronze-level spec content - METADATA ONLY
    spec_documents: List[SpecDocument] = field(default_factory=list)  # Keep, but content=None after streaming
    spec_sections: List[SpecSection] = field(default_factory=list)
    
    # Parsed spec metadata (not full openapi_spec)
    parsed_spec_info: Optional[Dict[str, Any]] = None  # NEW - just info, not full spec
    
    # Silver drafts (small, keep in memory)
    source_system: Optional[SourceSystem] = None
    endpoints: List[Endpoint] = field(default_factory=list)
    endpoint_parameters: List[EndpointParameter] = field(default_factory=list)
    schemas: List[Schema] = field(default_factory=list)
    schema_fields: List[SchemaField] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)
    relationships: List[EntityRelationship] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)

    # REMOVED: spec_chunk_embeddings - now in DB
    # REMOVED: doc_chunks - now in DB
    # REMOVED: openapi_spec - use parsed_spec_info for metadata
    
    # Gold drafts (small, keep in memory)
    workflow_template: Optional[WorkflowTemplate] = None
    integration_task: Optional[IntegrationTask] = None
    workflow_nodes: List[IntegrationFlowNode] = field(default_factory=list)
    workflow_edges: List[IntegrationFlowEdge] = field(default_factory=list)
    endpoint_bindings: List[EndpointBinding] = field(default_factory=list)
    policies: List[Policy] = field(default_factory=list)
    code_artifacts: List[CodeArtifact] = field(default_factory=list)

    # ... rest unchanged ...
    
    # Backward compatibility properties
    @property
    def doc_chunks(self) -> List[str]:
        """
        DEPRECATED: Access chunks via lazy_loader.iter_chunks() instead.
        
        This property exists for backward compatibility but triggers a warning.
        """
        import warnings
        warnings.warn(
            "state.doc_chunks is deprecated. Use lazy_loader.iter_chunks() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Return empty list - callers should use lazy loading
        return []
    
    @property
    def spec_chunk_embeddings(self) -> List:
        """
        DEPRECATED: Access embeddings via lazy_loader.iter_chunks(include_embedding=True).
        """
        import warnings
        warnings.warn(
            "state.spec_chunk_embeddings is deprecated. Use lazy_loader.iter_chunks() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return []
```

### 4.4 MODIFY: `graph/nodes/ingest_spec.py`

**Changes**:
1. Stream raw content to Bronze immediately
2. Stream chunks to Silver immediately
3. Clear content from memory after streaming
4. Store IDs instead of content

```python
# AFTER (proposed)
"""
ingest_spec node — fetches, streams, and chunks all spec_refs.

V3: Streaming persistence - writes to DB immediately, doesn't accumulate in memory.
"""
from pathlib import Path
import httpx
import hashlib
from typing import Iterator, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SpecDocument
from integration_coworker.persistence.streaming import (
    stream_raw_spec_to_bronze,
    stream_chunks_to_silver,
)
from integration_coworker.persistence.db import get_connection, get_engine_type, init_schema


def _fetch_spec_content(ref: str) -> tuple[bytes, str]:
    """
    Fetch content from a spec ref.
    Returns (content_bytes, content_type).
    """
    if ref.startswith("http://") or ref.startswith("https://"):
        response = httpx.get(ref, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        content = response.content  # bytes, not text
        content_type = response.headers.get("content-type", "application/octet-stream")
        return content, content_type
    else:
        file_path = Path(ref)
        if not file_path.exists():
            raise FileNotFoundError(f"Spec file not found: {ref}")
        content = file_path.read_bytes()
        suffix = file_path.suffix.lower()
        if suffix in [".yaml", ".yml"]:
            content_type = "application/yaml"
        elif suffix == ".json":
            content_type = "application/json"
        else:
            content_type = "text/plain"
        return content, content_type


def _chunk_content_iterator(content: str, chunk_size: int = 1000) -> Iterator[Tuple[int, str]]:
    """
    Yield (chunk_index, chunk_content) tuples.
    
    Generator - doesn't accumulate chunks in memory.
    """
    lines = content.split("\n")
    current_chunk = []
    current_size = 0
    chunk_index = 0

    for line in lines:
        current_chunk.append(line)
        current_size += len(line) + 1

        if current_size >= chunk_size or line.strip().startswith("paths:") or line.strip().startswith("components:"):
            if current_chunk:
                yield (chunk_index, "\n".join(current_chunk))
                chunk_index += 1
                current_chunk = []
                current_size = 0

    if current_chunk:
        yield (chunk_index, "\n".join(current_chunk))


def ingest_spec(state: WorkflowState) -> WorkflowState:
    """
    Ingest all spec_refs with streaming persistence.

    V3: Streams to DB immediately, doesn't accumulate in memory.
    
    Reads: spec_refs, plan
    Writes: spec_document_ids, raw_spec_ids, chunk_count, plan["chunk_index_to_spec_document_uri"]
    """
    if not state.spec_refs:
        state.errors.append("No spec_refs provided")
        state.completed_steps.append("ingest_spec")
        return state

    # Initialize schema for streaming writes
    init_schema()
    
    # Ensure we have a source_system_id
    source_system_id = _ensure_source_system(state.provider_code or "unknown")
    state.source_system_id = source_system_id
    
    total_chunks = 0

    for ref in state.spec_refs:
        try:
            # Fetch content
            content_bytes, content_type = _fetch_spec_content(ref)
            content_text = content_bytes.decode("utf-8")
            sha256 = hashlib.sha256(content_bytes).hexdigest()
            
            # 1. Stream raw content to Bronze immediately
            raw_spec_id = stream_raw_spec_to_bronze(
                content=content_bytes,
                uri=ref,
                content_type=content_type,
                source_system_id=source_system_id,
            )
            state.raw_spec_ids.append(raw_spec_id)
            
            # 2. Create SpecDocument metadata (content cleared after streaming)
            spec_doc_id = _upsert_spec_document(source_system_id, ref, sha256, content_type)
            state.spec_document_ids.append(spec_doc_id)
            
            # Create SpecDocument for state (without content - it's in DB)
            spec_doc = SpecDocument(
                id=spec_doc_id,
                source_system_id=source_system_id,
                version="1.0",
                uri=ref,
                content_type=content_type,
                sha256=sha256,
                content=None,  # Don't store content in memory
            )
            state.spec_documents.append(spec_doc)
            
            # 3. Stream chunks to Silver immediately
            chunk_count = stream_chunks_to_silver(
                chunks=_chunk_content_iterator(content_text),
                spec_document_id=spec_doc_id,
            )
            total_chunks += chunk_count

        except Exception as e:
            state.errors.append(f"Failed to ingest spec from {ref}: {str(e)}")

    state.chunk_count = total_chunks
    
    # Store spec document ID mapping in plan
    if state.plan is not None:
        state.plan["spec_document_ids"] = state.spec_document_ids
    
    state.completed_steps.append("ingest_spec")
    return state


def _ensure_source_system(provider_code: str) -> int:
    """Ensure source_system exists and return its ID."""
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                INSERT INTO spec_silver.source_systems (code, display_name)
                VALUES (%s, %s)
                ON CONFLICT (code) DO NOTHING
            """, (provider_code, provider_code.replace("_", " ").title()))
            cur.execute("SELECT id FROM spec_silver.source_systems WHERE code = %s", (provider_code,))
        else:
            cur.execute("""
                INSERT OR IGNORE INTO source_systems (code, display_name)
                VALUES (?, ?)
            """, (provider_code, provider_code.replace("_", " ").title()))
            cur.execute("SELECT id FROM source_systems WHERE code = ?", (provider_code,))
        
        source_system_id = cur.fetchone()[0]
        conn.commit()
        return source_system_id
    finally:
        conn.close()


def _upsert_spec_document(source_system_id: int, uri: str, sha256: str, content_type: str) -> int:
    """Upsert spec_document and return its ID."""
    engine = get_engine_type()
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        if engine == "postgres":
            cur.execute("""
                INSERT INTO spec_silver.spec_documents (source_system_id, uri, sha256, content_type)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (source_system_id, sha256) DO UPDATE SET uri = EXCLUDED.uri
                RETURNING id
            """, (source_system_id, uri, sha256, content_type))
            row = cur.fetchone()
            if row:
                spec_doc_id = row[0]
            else:
                cur.execute("""
                    SELECT id FROM spec_silver.spec_documents 
                    WHERE source_system_id = %s AND sha256 = %s
                """, (source_system_id, sha256))
                spec_doc_id = cur.fetchone()[0]
        else:
            cur.execute("""
                INSERT OR REPLACE INTO spec_documents (source_system_id, uri, sha256, content_type)
                VALUES (?, ?, ?, ?)
            """, (source_system_id, uri, sha256, content_type))
            cur.execute("""
                SELECT id FROM spec_documents WHERE source_system_id = ? AND sha256 = ?
            """, (source_system_id, sha256))
            spec_doc_id = cur.fetchone()[0]
        
        conn.commit()
        return spec_doc_id
    finally:
        conn.close()
```

### 4.5 MODIFY: `graph/nodes/embed_spec_chunks.py`

**Changes**:
1. Load chunks from DB instead of state
2. Stream embeddings to DB as computed
3. Don't accumulate embeddings in memory

```python
# AFTER (proposed)
"""
embed_spec_chunks node — generates embeddings with streaming persistence.

V3: Loads chunks from DB, streams embeddings back to DB.
"""
import os
import logging
from typing import Optional, List, Iterator, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.config import get_embedding_config, get_settings
from integration_coworker.persistence.lazy_loader import iter_chunks_for_embedding, get_chunk_count
from integration_coworker.persistence.streaming import stream_embeddings_to_chunks

logger = logging.getLogger(__name__)

# OpenAI embedding API limits
MAX_BATCH_SIZE = 100  # Smaller batches for streaming
MAX_INPUT_CHARS = 8000

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None


def _get_embedding_client() -> Optional["OpenAI"]:
    """Get OpenAI client if available and configured."""
    if not HAS_OPENAI:
        return None
    
    settings = get_settings()
    if not settings.llm.api_key or settings.llm.use_mock:
        return None
    
    try:
        kwargs = {"api_key": settings.llm.api_key}
        if settings.llm.base_url:
            kwargs["base_url"] = settings.llm.base_url
        return OpenAI(**kwargs)
    except Exception as e:
        logger.warning(f"Failed to create OpenAI client: {e}")
        return None


def _embed_batch(client: "OpenAI", texts: List[str], model: str) -> List[Optional[List[float]]]:
    """Embed a batch of texts. Returns list of embeddings (None for failures)."""
    try:
        response = client.embeddings.create(
            input=[t[:MAX_INPUT_CHARS] for t in texts],
            model=model,
        )
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]
    except Exception as e:
        logger.error(f"Embedding batch failed: {e}")
        return [None] * len(texts)


def _embedding_generator(
    client: "OpenAI",
    spec_document_ids: List[int],
    model: str,
    batch_size: int = MAX_BATCH_SIZE,
) -> Iterator[Tuple[int, List[float]]]:
    """
    Generator that yields (chunk_id, embedding) as they're computed.
    
    Processes chunks in batches but yields immediately for streaming.
    """
    for spec_document_id in spec_document_ids:
        batch_ids = []
        batch_texts = []
        
        for chunk_id, content in iter_chunks_for_embedding(spec_document_id):
            batch_ids.append(chunk_id)
            batch_texts.append(content)
            
            if len(batch_ids) >= batch_size:
                embeddings = _embed_batch(client, batch_texts, model)
                for cid, emb in zip(batch_ids, embeddings):
                    if emb is not None:
                        yield (cid, emb)
                batch_ids = []
                batch_texts = []
        
        # Process remaining batch
        if batch_ids:
            embeddings = _embed_batch(client, batch_texts, model)
            for cid, emb in zip(batch_ids, embeddings):
                if emb is not None:
                    yield (cid, emb)


def embed_spec_chunks(state: WorkflowState) -> WorkflowState:
    """
    Generate embeddings with streaming persistence.

    V3: Loads chunks from DB, streams embeddings back to DB.
    
    Reads: spec_document_ids (chunks are in DB)
    Writes: embedding_count (embeddings streamed to DB)
    """
    if not state.spec_document_ids:
        state.completed_steps.append("embed_spec_chunks")
        return state

    config = get_embedding_config()
    model = config.get("model", "text-embedding-3-small")

    client = _get_embedding_client()
    
    if not client:
        error_msg = (
            "Embedding client unavailable. "
            "Ensure OPENAI_API_KEY is set."
        )
        logger.error(error_msg)
        state.errors.append(error_msg)
        state.completed_steps.append("embed_spec_chunks")
        return state

    # Get total chunk count for logging
    total_chunks = sum(get_chunk_count(doc_id) for doc_id in state.spec_document_ids)
    logger.info(f"Generating embeddings for {total_chunks} chunks using {model}")
    
    # Stream embeddings to DB as they're computed
    embedding_count = stream_embeddings_to_chunks(
        embeddings=_embedding_generator(client, state.spec_document_ids, model),
    )
    
    state.embedding_count = embedding_count
    
    if embedding_count < total_chunks:
        state.warnings.append(
            f"{total_chunks - embedding_count}/{total_chunks} chunks failed to embed"
        )

    state.completed_steps.append("embed_spec_chunks")
    return state
```

### 4.6 MODIFY: `graph/nodes/persist_silver_checkpoint.py`

**Changes**:
1. Skip chunk/embedding INSERT (already in DB from streaming)
2. Only persist metadata (endpoints, schemas, entities, etc.)
3. Much faster execution

```python
# Key change in persist_silver_checkpoint:

def persist_silver_checkpoint(state: WorkflowState) -> WorkflowState:
    """
    Persist Silver layer METADATA to database.
    
    V3: Chunks and embeddings are already in DB from streaming.
    This node only persists:
    - endpoints, endpoint_parameters
    - schemas, schema_fields
    - entities, entity_relationships
    - events
    - spec_sections
    
    REMOVED: spec_chunks INSERT (handled by streaming.stream_chunks_to_silver)
    REMOVED: embeddings INSERT (handled by streaming.stream_embeddings_to_chunks)
    """
    # ... existing code for endpoints, schemas, entities ...
    
    # REMOVED: Section 11 (Insert SpecChunks with embeddings)
    # Chunks are already in DB from ingest_spec streaming
    # Embeddings are already in DB from embed_spec_chunks streaming
    
    # Update persisted_ids with chunk count from state
    state.persisted_ids.update({
        "silver_checkpoint": "completed",
        "source_system_id": source_system_id,
        "spec_document_ids": state.spec_document_ids,
        "endpoint_count": len(state.endpoints),
        "schema_count": len(state.schemas),
        "entity_count": len(state.entities),
        "chunk_count": state.chunk_count,  # From streaming
        "embedding_count": state.embedding_count,  # From streaming
        "silver_timestamp": datetime.now(timezone.utc).isoformat(),
    })
    
    # ... rest unchanged ...
```

### 4.7 MODIFY: `graph/nodes/build_report.py`

**Changes**: Use metadata counts instead of len(state.doc_chunks)

```python
# BEFORE
lines.append(f"- Chunks: {len(state.doc_chunks)}")

# AFTER
lines.append(f"- Chunks: {state.chunk_count}")
lines.append(f"- Embeddings: {state.embedding_count}")
```

### 4.8 MODIFY: `graph/nodes/persist_run_outcome.py`

**Changes**: Use metadata counts instead of len(state.spec_chunk_embeddings)

```python
# BEFORE
if state.spec_chunk_embeddings:
    # ...
    len(state.spec_chunk_embeddings)

# AFTER
if state.embedding_count > 0:
    # ...
    state.embedding_count
```

---

## 5. Database Schema Additions

### 5.1 Bronze Layer Table (if not exists)

```sql
-- spec_bronze.raw_specs (already defined in postgres.py)
CREATE TABLE IF NOT EXISTS spec_bronze.raw_specs (
    id BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT REFERENCES spec_silver.source_systems(id),
    uri TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content_type TEXT,
    raw_content BYTEA,  -- Store raw bytes
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_specs_sha256 ON spec_bronze.raw_specs(sha256);
```

### 5.2 Verify spec_chunks has embedding column

```sql
-- Ensure spec_chunks can store embeddings (should already exist)
ALTER TABLE spec_silver.spec_chunks 
ADD COLUMN IF NOT EXISTS embedding vector(1536);

CREATE INDEX IF NOT EXISTS idx_spec_chunks_embedding 
ON spec_silver.spec_chunks USING ivfflat (embedding vector_cosine_ops);
```

---

## 6. Migration Path

### 6.1 Phase 1: Foundation (Non-Breaking)

**Goal**: Add new files without changing existing behavior.

1. Create `persistence/streaming.py`
2. Create `persistence/lazy_loader.py`
3. Add helper functions to `persistence/db.py`
4. All existing tests pass unchanged

**Validation**:
```bash
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v
```

### 6.2 Phase 2: Opt-In Streaming

**Goal**: Add streaming mode behind a feature flag.

1. Add `STREAMING_PERSISTENCE=true` environment variable
2. Modify `ingest_spec.py` to use streaming when enabled
3. Modify `embed_spec_chunks.py` to use streaming when enabled
4. Keep old code path as fallback

**Validation**:
```bash
# Old path (default)
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v

# New path
STREAMING_PERSISTENCE=true USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v
```

### 6.3 Phase 3: State Slimming

**Goal**: Update WorkflowState to slim model.

1. Add deprecation warnings to old fields
2. Add new fields (spec_document_ids, chunk_count, etc.)
3. Update downstream nodes to use new fields
4. Update tests

**Validation**:
```bash
pytest tests/ -v --tb=short 2>&1 | grep -i deprecat  # Check for deprecation warnings
```

### 6.4 Phase 4: Default Streaming

**Goal**: Make streaming the default.

1. Flip `STREAMING_PERSISTENCE` default to True
2. Remove old code path
3. Remove deprecation warnings (error on old field access)
4. Update all documentation

**Validation**:
```bash
pytest tests/ -v  # All tests pass with streaming default
```

---

## 7. Testing Strategy

### 7.1 New Unit Tests

```python
# tests/test_streaming_persistence.py

def test_stream_raw_spec_to_bronze():
    """Test Bronze layer streaming."""
    pass

def test_stream_chunks_to_silver():
    """Test chunk streaming."""
    pass

def test_stream_embeddings_to_chunks():
    """Test embedding streaming."""
    pass

def test_streaming_is_idempotent():
    """Test that re-streaming same content doesn't duplicate."""
    pass


# tests/test_lazy_loader.py

def test_iter_chunks_pagination():
    """Test paginated chunk iteration."""
    pass

def test_get_chunk_by_id():
    """Test single chunk loading."""
    pass

def test_iter_chunks_for_embedding():
    """Test loading only un-embedded chunks."""
    pass
```

### 7.2 Integration Tests

```python
# tests/test_streaming_integration.py

def test_large_spec_streaming():
    """Test with a spec that has 1000+ endpoints."""
    pass

def test_memory_usage_with_streaming():
    """Verify memory stays bounded during large spec processing."""
    pass

def test_streaming_with_network_failure():
    """Test resumption after partial streaming."""
    pass
```

### 7.3 Memory Profiling

```python
# tests/test_memory_profile.py

@pytest.mark.slow
def test_memory_stays_bounded():
    """
    Process a large spec and verify memory doesn't exceed threshold.
    
    Uses memory_profiler to track peak memory usage.
    """
    import tracemalloc
    
    tracemalloc.start()
    
    # Process large spec
    result = design_and_generate_integration(
        spec_refs=["tests/fixtures/large_spec_10k_endpoints.yaml"],
        task_description="Test memory usage",
    )
    
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    
    # Peak memory should be < 50MB even for large spec
    assert peak < 50 * 1024 * 1024, f"Peak memory {peak / 1024 / 1024:.1f}MB exceeds 50MB"
```

---

## 8. Rollback Plan

If issues are discovered after deployment:

### 8.1 Immediate Rollback

```bash
# Set environment variable to disable streaming
export STREAMING_PERSISTENCE=false

# Restart application
```

### 8.2 Data Recovery

Streaming writes are idempotent and use the same tables. No data migration needed.

### 8.3 Full Revert

```bash
git revert <streaming-persistence-commit>
```

---

## 9. Performance Expectations

### 9.1 Memory Usage

| Spec Size | Current | With Streaming |
|-----------|---------|----------------|
| 100 endpoints | 10MB | 3MB |
| 1,000 endpoints | 50MB | 5MB |
| 10,000 endpoints | 200MB+ | 10MB |
| 50,000 endpoints | OOM | 15MB |

### 9.2 Execution Time

| Spec Size | Current | With Streaming | Notes |
|-----------|---------|----------------|-------|
| 100 endpoints | 5s | 6s | Slightly slower (more DB writes) |
| 1,000 endpoints | 30s | 35s | Slightly slower |
| 10,000 endpoints | 5min | 4min | Faster (no memory pressure) |
| 50,000 endpoints | N/A (OOM) | 15min | Now possible |

### 9.3 Database Load

- **Writes**: More frequent, smaller batches
- **Reads**: More (lazy loading), but indexed
- **Connections**: Same (pooled)

---

## 10. Comparison: Alternative Approaches Considered

### 10.1 Alternative A: Per-Node Persistence

**Description**: Each node writes its outputs to DB, reads inputs from DB.

**Pros**:
- Maximum observability
- Fine-grained recovery

**Cons**:
- Fights LangGraph's state-passing design
- Every node needs DB read/write logic
- Slower (many small transactions)
- Complex rollback on failure

**Decision**: Rejected. Too invasive, doesn't leverage LangGraph strengths.

### 10.2 Alternative B: External State Store (Redis)

**Description**: Use Redis for large fields, Postgres for metadata.

**Pros**:
- Very fast reads/writes
- Built-in TTL for cleanup

**Cons**:
- Another infrastructure dependency
- No ACID guarantees
- Data split across two stores

**Decision**: Rejected. Adds complexity without solving core problem.

### 10.3 Alternative C: Streaming to Object Storage (S3)

**Description**: Stream large data to S3, keep references in Postgres.

**Pros**:
- Unlimited storage
- Cost-effective for large specs

**Cons**:
- Network latency for every access
- Another infrastructure dependency
- Eventual consistency concerns

**Decision**: Rejected. Overkill for current scale; could revisit at petabyte scale.

### 10.4 Selected: Streaming Persistence + Lazy Loading

**Description**: Stream to Postgres immediately, load lazily via indexed queries.

**Pros**:
- Single data store (Postgres)
- ACID guarantees
- Leverages existing pgvector indexes
- Minimal code changes
- LangGraph-compatible

**Cons**:
- More DB writes (mitigated by batching)
- Requires careful lazy loading in downstream nodes

**Decision**: Selected. Best balance of simplicity, reliability, and LangGraph compatibility.

---

## 11. Open Questions

1. **Batch size tuning**: What's the optimal batch size for streaming? (Proposed: 100 chunks, 50 embeddings)

2. **Connection pooling**: Should we use a connection pool for streaming writes? (Recommended: Yes, via SQLAlchemy or psycopg2 pool)

3. **Partial failure handling**: If embedding fails mid-stream, how do we resume? (Proposed: `iter_chunks_for_embedding` only yields un-embedded chunks)

4. **Dry-run mode**: How does streaming interact with `dry_run=True`? (Proposed: Skip streaming, use in-memory fallback)

5. **Test fixtures**: How do we mock streaming for unit tests? (Proposed: Dedicated SQLite test DB per test)

---

## 12. Appendix: File Change Checklist

### New Files
- [ ] `persistence/streaming.py`
- [ ] `persistence/lazy_loader.py`
- [ ] `tests/test_streaming_persistence.py`
- [ ] `tests/test_lazy_loader.py`
- [ ] `tests/test_memory_profile.py`

### Modified Files
- [ ] `graph/state.py`
- [ ] `graph/nodes/ingest_spec.py`
- [ ] `graph/nodes/embed_spec_chunks.py`
- [ ] `graph/nodes/persist_silver_checkpoint.py`
- [ ] `graph/nodes/build_report.py`
- [ ] `graph/nodes/persist_run_outcome.py`
- [ ] `persistence/db.py`
- [ ] `persistence/postgres.py`
- [ ] `tests/conftest.py`

### Documentation Updates
- [ ] `docs/architecture_overview.md`
- [ ] `README.md` (environment variables)
- [ ] `CHANGELOG.md`

---

## 13. Sign-Off

| Role | Name | Date | Approval |
|------|------|------|----------|
| Author | Copilot | 2025-12-05 | ✓ |
| Technical Review | | | |
| Architecture Review | | | |
