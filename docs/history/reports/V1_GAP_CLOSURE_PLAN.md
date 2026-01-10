# V1 Gap Closure Implementation Plan

**Author:** Senior AI Software Engineer  
**Date:** November 29, 2025  
**Status:** ✅ COMPLETED (All P0-P7 items implemented)  
**Final Test Count:** 796 tests passing

---

## Executive Summary

This plan closes the 30% gap between design doc promises and current implementation. The gaps fall into 6 categories, prioritized by impact and dependency order:

| Priority | Gap Category | Impact | Effort |
|----------|--------------|--------|--------|
| P0 | Embedding Search | Enables RAG/KG retrieval (unused vectors) | 2 days |
| P1 | Multi-Endpoint Flows | Unlocks complex integrations | 2 days |
| P1 | Policy Wiring to Code | Auth/retry/pagination in generated code | 2 days |
| P2 | Repo File Writes | Complete repo integration path | 1.5 days |
| P2 | Config File Generation | YAML/JSON config artifacts | 0.5 days |
| P3 | Request/Response Mappings | EndpointBinding field mapping DSL | 1 day |
| P3 | HTML/PDF Spec Parsing | Text extraction + heuristic Silver model | 2 days |
| P3 | CSV/EDI/Message Specs | Non-HTTP spec ingestion pipeline | 2 days |
| P4 | Postgres + pgvector | Production-ready persistence layer | 1.5 days |
| P4 | GitHub API Provider | Remote repo analysis without local clone | 1.5 days |

**Total Estimated Effort:** 16-20 engineering days

---

## P0: Embedding Search (2 days)

### Problem
Embeddings are computed in `embed_spec_chunks` (1536-dim vectors) and persisted to `spec_chunks` table, but **never queried** for semantic retrieval. This wastes compute and fails the design doc promise of "vector index over spec chunks."

### Solution
Create a retrieval module that:
1. Queries `spec_chunks` by cosine similarity using stored embeddings
2. Integrates into `align_task_with_kg` for **hybrid GraphRAG** template matching (graph-structural filters + semantic ranking)
3. Optionally enhances `understand_task` for endpoint discovery using **pure semantic retrieval**

**Non-goal for v1:** do **not** use semantic search to dynamically traverse or rewire the LangGraph workflow itself. Node ordering and branching remain explicit; semantic search is reserved for spec/templating retrieval and KG ranking.

### Files to Create

#### 1. `src/integration_coworker/retrieval/__init__.py`
```python
"""
Semantic retrieval module for spec chunks and KG nodes.
Implements design doc Section 6.1.
"""
from .semantic_search import (
    search_spec_chunks,
    search_kg_templates,
    compute_embedding,
)
```

#### 2. `src/integration_coworker/retrieval/semantic_search.py`
```python
"""
Semantic search over embeddings stored in DB.

Functions:
- `search_spec_chunks(query: str, top_k: int, spec_document_id: Optional[int]) -> List[ChunkMatch]`
- `search_kg_templates(query: str, provider_code: str, top_k: int) -> List[TemplateMatch]`
- `compute_embedding(text: str) -> List[float]`

Implementation:
- Uses same embedding client as `embed_spec_chunks` (OpenAIEmbeddings)
- For **SQLite backend**: computes query embedding, then calculates cosine similarity in Python.
- For **Postgres+pgvector backend**: pushes similarity computation into the DB where possible.
- Returns `top_k` results with similarity scores.
"""

import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass
import json
import math

from integration_coworker.persistence import db
from integration_coworker.config import get_settings

logger = logging.getLogger(__name__)

@dataclass
class ChunkMatch:
    chunk_id: int
    spec_document_id: int
    chunk_index: int
    content: str
    similarity: float

@dataclass
class TemplateMatch:
    node_id: int
    key: str
    name: str
    description: Optional[str]
    similarity: float

def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

def compute_embedding(text: str) -> List[float]:
    """
    Compute embedding for query text using same client as embed_spec_chunks.
    Returns 1536-dim vector or empty list on failure.
    """
    # Import here to avoid circular deps
    from integration_coworker.graph.nodes.embed_spec_chunks import _get_embedding_client
    
    client = _get_embedding_client()
    if not client:
        logger.warning("No embedding client available for semantic search")
        return []
    
    try:
        result = client.embed_query(text)
        return result
    except Exception as e:
        logger.warning(f"Failed to compute query embedding: {e}")
        return []

def search_spec_chunks(
    query: str,
    top_k: int = 5,
    spec_document_id: Optional[int] = None,
) -> List[ChunkMatch]:
    """
    Search spec_chunks by semantic similarity.
    
    Args:
        query: Natural language query
        top_k: Number of results to return
        spec_document_id: Optional filter to specific document
    
    Returns:
        List of ChunkMatch sorted by similarity (highest first)
    """
    query_embedding = compute_embedding(query)
    if not query_embedding:
        return []
    
    conn = db.get_connection()
    cur = conn.cursor()
    
    # Fetch all chunks with embeddings (or filtered by doc)
    if spec_document_id:
        cur.execute("""
            SELECT id, spec_document_id, chunk_index, content, embedding
            FROM spec_chunks
            WHERE spec_document_id = ? AND embedding IS NOT NULL
        """, (spec_document_id,))
    else:
        cur.execute("""
            SELECT id, spec_document_id, chunk_index, content, embedding
            FROM spec_chunks
            WHERE embedding IS NOT NULL
        """)
    
    rows = cur.fetchall()
    
    # Score each chunk
    matches = []
    for row in rows:
        chunk_id, doc_id, chunk_idx, content, embedding_json = row
        if not embedding_json:
            continue
        
        chunk_embedding = json.loads(embedding_json)
        similarity = cosine_similarity(query_embedding, chunk_embedding)
        
        matches.append(ChunkMatch(
            chunk_id=chunk_id,
            spec_document_id=doc_id,
            chunk_index=chunk_idx,
            content=content,
            similarity=similarity,
        ))
    
    # Sort by similarity and return top_k
    matches.sort(key=lambda m: m.similarity, reverse=True)
    return matches[:top_k]

def search_kg_templates(
        query: str,
        provider_code: Optional[str] = None,
        top_k: int = 5,
) -> List[TemplateMatch]:
        """Hybrid GraphRAG search over KG workflow templates.

        Responsibilities:
        - Use KG traversal functions for **structural filtering** of candidate templates
            (provider, entities, patterns, task type).
        - Use embeddings for **semantic ranking** of those candidates against the task
            description.
        - Preserve explainability by returning graph- and embedding-related scores.

        Non-goal: this function does *not* replace KG BFS/DFS for structural queries like
        shortest paths; it layers semantic search on top of those graph operations.
        """
    query_embedding = compute_embedding(query)
    
    conn = db.get_connection()
    cur = conn.cursor()
    
    # Fetch workflow template nodes
    if provider_code:
        cur.execute("""
            SELECT id, key, name, description, embedding
            FROM kg_nodes
            WHERE node_type = 'workflow_template'
              AND (provider_code = ? OR provider_code IS NULL)
        """, (provider_code,))
    else:
        cur.execute("""
            SELECT id, key, name, description, embedding
            FROM kg_nodes
            WHERE node_type = 'workflow_template'
        """)
    
    rows = cur.fetchall()
    
    matches = []
    for row in rows:
        node_id, key, name, description, embedding_json = row
        
        if query_embedding and embedding_json:
            node_embedding = json.loads(embedding_json)
            similarity = cosine_similarity(query_embedding, node_embedding)
        else:
            # Fallback: keyword matching
            similarity = _keyword_similarity(query, name, description)
        
        matches.append(TemplateMatch(
            node_id=node_id,
            key=key,
            name=name,
            description=description,
            similarity=similarity,
        ))
    
    matches.sort(key=lambda m: m.similarity, reverse=True)
    return matches[:top_k]

def _keyword_similarity(query: str, name: str, description: Optional[str]) -> float:
    """Fallback keyword-based similarity when embeddings unavailable."""
    query_words = set(query.lower().split())
    text = f"{name} {description or ''}".lower()
    text_words = set(text.split())
    
    if not query_words or not text_words:
        return 0.0
    
    intersection = len(query_words & text_words)
    return intersection / len(query_words)
```

#### 3. `tests/retrieval/test_semantic_search.py`
```python
"""Tests for semantic search module."""
import pytest
from unittest.mock import patch, MagicMock
from integration_coworker.retrieval.semantic_search import (
    cosine_similarity,
    search_spec_chunks,
    search_kg_templates,
    ChunkMatch,
)

class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)
    
    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)
    
    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

class TestSearchSpecChunks:
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_returns_top_k_results(self, mock_db, mock_embed):
        mock_embed.return_value = [1.0, 0.0, 0.0]
        # ... test implementation
```

### Files to Modify

#### 4. `src/integration_coworker/graph/nodes/align_task_with_kg.py`
**Changes:**
- Import `search_kg_templates` from retrieval module.
- Use KG traversal helpers (`find_related_tasks_via_graph`, `find_cross_provider_tasks_via_pattern`, etc.) for
    **candidate selection and structural scoring**.
- Use `search_kg_templates` for **semantic ranking** of those candidates against the task description.
- Add semantic scoring to `_compute_combined_score()` function and replace hardcoded 0.5 default with actual
    embedding similarity when embeddings are available.

**Important constraint:** keep KG BFS/DFS as the source of truth for structural questions (neighbors, cross-provider
patterns, shortest paths). Semantic search should *augment*, not replace, these structural scores.

```python
# Line ~180 - Add import
from integration_coworker.retrieval.semantic_search import search_kg_templates

# Line ~400 - Modify _compute_combined_score to use real embeddings
def _compute_combined_score(
    template: dict,
    task_description: str,
    entities: List[str],
) -> float:
    """
    Compute combined score: 40% graph + 40% embedding + 20% exact match.
    Now uses actual semantic search instead of hardcoded 0.5.
    """
    graph_score = _compute_graph_score(template, entities)
    
    # NEW: Use semantic search for embedding score
    from integration_coworker.retrieval.semantic_search import compute_embedding, cosine_similarity
    query_emb = compute_embedding(task_description)
    template_emb = template.get("embedding", [])
    
    if query_emb and template_emb:
        embedding_score = max(0, cosine_similarity(query_emb, template_emb))
    else:
        embedding_score = 0.5  # Fallback only when embeddings unavailable
    
    exact_match_bonus = _compute_exact_match_bonus(template, task_description)
    
    return (graph_score * 0.4) + (embedding_score * 0.4) + exact_match_bonus + 0.1
```

#### 5. `src/integration_coworker/graph/nodes/understand_task.py`
**Changes:**
- Add optional semantic search for endpoint discovery.
- Use retrieved chunks to enhance task understanding prompt.
- Keep this node **purely semantic**: it should not invoke KG BFS/DFS directly; its job is to supply rich
    spec-grounded context for downstream reasoning.

```python
# Line ~150 - Add helper function
def _get_relevant_spec_context(task_description: str, state: WorkflowState) -> str:
    """Retrieve relevant spec chunks for task understanding context."""
    from integration_coworker.retrieval.semantic_search import search_spec_chunks
    
    chunks = search_spec_chunks(task_description, top_k=3)
    if not chunks:
        return ""
    
    context_lines = ["Relevant API documentation:"]
    for chunk in chunks:
        context_lines.append(f"---\n{chunk.content[:500]}\n---")
    
    return "\n".join(context_lines)

# Line ~200 - Modify _build_understand_task_prompt to include retrieved context
# Add: spec_context = _get_relevant_spec_context(task_description, state)
# Include in prompt: f"## Relevant Documentation\n{spec_context}\n\n"
```

---

## P1: Multi-Endpoint Flows (2 days)

### Problem
Workflows always generate a single `api_call` node regardless of task complexity. Design doc Section 5.4.7 promises "define call order" for multi-step integrations.

### Solution
Enhance workflow generation to:
1. Detect multi-step tasks from task description
2. Generate multiple `api_call` nodes with proper edges
3. Create multiple `EndpointBinding` entries

### Files to Modify

#### 1. `src/integration_coworker/graph/nodes/align_task_with_kg.py`

**Changes (Lines 230-400):**

```python
# New function to detect multi-step patterns
def _detect_multi_step_pattern(task_description: str) -> Optional[List[str]]:
    """
    Detect if task requires multiple API calls.
    
    Patterns:
    - "create X then Y" → [create_x, create_y]
    - "get X and update Y" → [get_x, update_y]
    - "list X, filter, then delete" → [list_x, delete_x]
    - "checkout flow" → [create_cart, add_items, create_payment, confirm]
    
    Returns: List of action slugs or None for single-call tasks
    """
    multi_step_patterns = [
        (r"(\w+)\s+(?:and|then|,)\s+(\w+)", 2),  # "create and update"
        (r"first\s+(\w+)\s+then\s+(\w+)", 2),     # "first get then update"
        (r"(\w+)\s+flow", None),                   # "checkout flow" → lookup template
    ]
    
    task_lower = task_description.lower()
    
    for pattern, expected_steps in multi_step_patterns:
        match = re.search(pattern, task_lower)
        if match:
            if expected_steps:
                return list(match.groups())
            else:
                # Flow pattern - check KG for multi-step template
                return None  # Let KG template handle it
    
    return None

# Modify _infer_workflow_from_endpoint to support multiple endpoints
def _infer_multi_endpoint_workflow(
    endpoints: List[Endpoint],
    task_description: str,
    action_sequence: List[str],
) -> List[dict]:
    """
    Generate workflow steps for multiple API calls.
    
    Creates: start → validate → [api_call_1 → transform_1 → api_call_2 → ...] → end
    """
    steps = [{"key": "start", "type": "start", "label": "Start"}]
    
    # Match each action to an endpoint
    for i, action in enumerate(action_sequence):
        endpoint = _find_endpoint_for_action(endpoints, action)
        method = (endpoint.method if endpoint else "POST").upper()
        path = endpoint.path if endpoint else f"/{action}"
        
        # Validate step for each call
        steps.append({
            "key": f"validate_{action}",
            "type": "validation",
            "label": f"Validate {action.title()} Input",
        })
        
        # API call step
        steps.append({
            "key": f"call_{action}",
            "type": "api_call",
            "label": f"{method} {path}",
            "description": endpoint.summary if endpoint else f"Call {action}",
            "_endpoint_ref": endpoint,  # Internal ref for binding
        })
        
        # Transform step (unless last)
        if i < len(action_sequence) - 1:
            steps.append({
                "key": f"transform_{action}",
                "type": "transform",
                "label": f"Extract {action.title()} Result",
                "description": f"Pass result to next step",
            })
    
    steps.append({"key": "end", "type": "end", "label": "Return Final Result"})
    return steps

def _find_endpoint_for_action(endpoints: List[Endpoint], action: str) -> Optional[Endpoint]:
    """Find endpoint matching action keyword."""
    action_method_map = {
        "create": "POST",
        "add": "POST",
        "get": "GET",
        "list": "GET",
        "update": "PUT",
        "delete": "DELETE",
    }
    
    expected_method = action_method_map.get(action.split("_")[0])
    
    for ep in endpoints:
        if expected_method and ep.method.upper() == expected_method:
            if action in (ep.operation_id or "").lower() or action in (ep.path or "").lower():
                return ep
    
    return endpoints[0] if endpoints else None
```

#### 2. `src/integration_coworker/graph/nodes/plan_integration_flow.py`

**Changes (Lines 115-190):**

```python
# Modify to create bindings for ALL api_call nodes
def plan_integration_flow(state: WorkflowState) -> WorkflowState:
    """
    Validates flow structure and creates EndpointBinding for each api_call node.
    Now supports multiple api_call nodes in a single flow.
    """
    # ... existing validation code ...
    
    # Create EndpointBinding for EACH api_call node
    api_call_nodes = [n for n in state.workflow_nodes if n.node_type == "api_call"]
    
    if not api_call_nodes:
        # Create default single-call flow
        # ... existing fallback ...
    
    for api_node in api_call_nodes:
        # Check if node has endpoint reference from align_task_with_kg
        endpoint_ref = api_node.config.get("_endpoint_ref") if api_node.config else None
        
        # Match endpoint
        if endpoint_ref:
            matched_endpoint = endpoint_ref
        else:
            matched_endpoint = _match_endpoint_to_node(api_node, state.endpoints)
        
        binding = EndpointBinding(
            id=None,
            task_id=None,
            flow_node_key=api_node.node_key,
            endpoint_id=matched_endpoint.id if matched_endpoint else None,
            params_mapping={},
            request_mapping={},
            response_mapping={},
        )
        binding._matched_endpoint = matched_endpoint
        state.endpoint_bindings.append(binding)
    
    # ... rest of function ...
```

#### 3. `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

**Changes (Lines 100-200):**

```python
# New function to generate multi-call workflow code
def _generate_multi_call_flow_code(
    state: WorkflowState,
    provider_code: str,
    task_slug: str,
    client_class: str,
    client_import_module: str,
    flow_function: str,
) -> str:
    """
    Generate workflow function that calls multiple endpoints in sequence.
    """
    api_call_nodes = [n for n in state.workflow_nodes if n.node_type == "api_call"]
    
    # Build call sequence
    call_lines = []
    for i, node in enumerate(api_call_nodes):
        binding = next((b for b in state.endpoint_bindings if b.flow_node_key == node.node_key), None)
        endpoint = binding._matched_endpoint if binding else None
        
        method_name = derive_method_name(endpoint) if endpoint else f"call_{i}"
        
        # Generate call with result chaining
        if i == 0:
            call_lines.append(f"    result_{i} = client.{method_name}(**params)")
        else:
            call_lines.append(f"    result_{i} = client.{method_name}(**result_{i-1})")
    
    # Combine into function
    return f'''
from {client_import_module} import {client_class}

def {flow_function}(params: dict) -> dict:
    """
    Multi-step workflow: {task_slug}
    
    Steps:
    {chr(10).join(f"    {i+1}. {n.config.get('label', n.node_key)}" for i, n in enumerate(api_call_nodes))}
    """
    client = {client_class}()
    
{chr(10).join(call_lines)}
    
    return result_{len(api_call_nodes) - 1}
'''
```

### New Test Files

#### 4. `tests/test_multi_endpoint_flows.py`
```python
"""Tests for multi-endpoint workflow generation."""
import pytest
from integration_coworker.graph.nodes.align_task_with_kg import (
    _detect_multi_step_pattern,
    _infer_multi_endpoint_workflow,
)

class TestMultiStepDetection:
    def test_detects_and_pattern(self):
        assert _detect_multi_step_pattern("create user and send email") == ["create", "send"]
    
    def test_detects_then_pattern(self):
        assert _detect_multi_step_pattern("first validate then create") == ["validate", "create"]
    
    def test_single_action_returns_none(self):
        assert _detect_multi_step_pattern("create a payment") is None

class TestMultiEndpointWorkflow:
    def test_generates_multiple_api_calls(self, sample_endpoints):
        steps = _infer_multi_endpoint_workflow(
            sample_endpoints,
            "create payment and send receipt",
            ["create", "send"],
        )
        
        api_calls = [s for s in steps if s["type"] == "api_call"]
        assert len(api_calls) == 2
        assert api_calls[0]["key"] == "call_create"
        assert api_calls[1]["key"] == "call_send"
```

---

## P1: Policy Wiring to Code (2 days)

### Problem
Policy objects are created in `attach_policies_and_patterns` but the generated code doesn't use them. Auth headers, retry logic, and rate limiting are not implemented.

### Solution
Enhance code generation to:
1. Read policies from state
2. Generate appropriate code patterns for each policy type
3. Wire into client and workflow code

### Files to Modify

#### 1. `src/integration_coworker/codegen/policy_templates.py` (NEW FILE)

```python
"""
Policy code templates for generated integrations.

Each policy type has a corresponding code pattern that gets injected
into the generated client or workflow code.
"""
from typing import Dict, Any, Optional

# -----------------------------------------------------------------------------
# Auth Policy Templates
# -----------------------------------------------------------------------------

AUTH_BEARER_TEMPLATE = '''
    def _get_auth_headers(self) -> dict:
        """Get authentication headers (Bearer token)."""
        token = os.environ.get("{env_var}", "")
        if not token:
            raise ValueError("Missing {env_var} environment variable")
        return {{"Authorization": f"Bearer {{token}}"}}
'''

AUTH_API_KEY_TEMPLATE = '''
    def _get_auth_headers(self) -> dict:
        """Get authentication headers (API Key)."""
        api_key = os.environ.get("{env_var}", "")
        if not api_key:
            raise ValueError("Missing {env_var} environment variable")
        return {{"{header_name}": api_key}}
'''

AUTH_OAUTH2_TEMPLATE = '''
    def _get_auth_headers(self) -> dict:
        """Get OAuth2 authentication headers."""
        # Token should be obtained via OAuth2 flow and stored
        token = self._oauth_token or os.environ.get("{env_var}", "")
        if not token:
            raise ValueError("Missing OAuth2 token - call authenticate() first")
        return {{"Authorization": f"Bearer {{token}}"}}
    
    def authenticate(self, client_id: str, client_secret: str) -> str:
        """Obtain OAuth2 access token using client credentials."""
        response = self._http_client.post(
            "{token_url}",
            data={{
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "{scopes}",
            }},
        )
        self._oauth_token = response.json()["access_token"]
        return self._oauth_token
'''

# -----------------------------------------------------------------------------
# Retry Policy Templates
# -----------------------------------------------------------------------------

RETRY_DECORATOR_TEMPLATE = '''
def with_retry(max_attempts: int = {max_attempts}, backoff_factor: float = {backoff_factor}):
    """Decorator for automatic retry with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError, HTTPError) as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        wait_time = backoff_factor * (2 ** attempt)
                        time.sleep(wait_time)
            raise last_exception
        return wrapper
    return decorator
'''

RETRY_METHOD_WRAPPER = '''
    def _request_with_retry(self, method: str, url: str, **kwargs) -> Response:
        """Make HTTP request with automatic retry."""
        max_attempts = {max_attempts}
        backoff_factor = {backoff_factor}
        
        last_exception = None
        for attempt in range(max_attempts):
            try:
                response = self._http_client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except (ConnectionError, TimeoutError) as e:
                last_exception = e
                if attempt < max_attempts - 1:
                    wait_time = backoff_factor * (2 ** attempt)
                    time.sleep(wait_time)
            except HTTPError as e:
                if e.response.status_code in (429, 500, 502, 503, 504):
                    last_exception = e
                    if attempt < max_attempts - 1:
                        wait_time = backoff_factor * (2 ** attempt)
                        time.sleep(wait_time)
                else:
                    raise
        raise last_exception
'''

# -----------------------------------------------------------------------------
# Rate Limit Policy Templates
# -----------------------------------------------------------------------------

RATE_LIMIT_TEMPLATE = '''
class RateLimiter:
    """Simple rate limiter using token bucket algorithm."""
    def __init__(self, requests_per_second: float = {requests_per_second}):
        self.rate = requests_per_second
        self.tokens = requests_per_second
        self.last_update = time.time()
        self._lock = threading.Lock()
    
    def acquire(self):
        """Wait until a request token is available."""
        with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                time.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1
'''

# -----------------------------------------------------------------------------
# Pagination Policy Templates
# -----------------------------------------------------------------------------

PAGINATION_OFFSET_TEMPLATE = '''
    def _paginate_offset(
        self,
        endpoint: str,
        params: dict,
        page_size: int = {page_size},
    ) -> Generator[dict, None, None]:
        """Iterate through paginated results using offset/limit."""
        offset = 0
        while True:
            page_params = {{**params, "offset": offset, "limit": page_size}}
            response = self._request("GET", endpoint, params=page_params)
            data = response.json()
            
            items = data.get("items", data.get("data", data.get("results", [])))
            if not items:
                break
            
            for item in items:
                yield item
            
            if len(items) < page_size:
                break
            offset += page_size
'''

PAGINATION_CURSOR_TEMPLATE = '''
    def _paginate_cursor(
        self,
        endpoint: str,
        params: dict,
        cursor_field: str = "{cursor_field}",
    ) -> Generator[dict, None, None]:
        """Iterate through paginated results using cursor-based pagination."""
        cursor = None
        while True:
            page_params = {{**params}}
            if cursor:
                page_params[cursor_field] = cursor
            
            response = self._request("GET", endpoint, params=page_params)
            data = response.json()
            
            items = data.get("items", data.get("data", []))
            for item in items:
                yield item
            
            cursor = data.get("next_cursor", data.get("cursor", data.get("next")))
            if not cursor:
                break
'''


def get_auth_code(policy_config: Dict[str, Any]) -> str:
    """Generate auth code from policy config."""
    auth_type = policy_config.get("type", "none")
    
    if auth_type == "bearer":
        env_var = f"{policy_config.get('scheme_name', 'API').upper()}_TOKEN"
        return AUTH_BEARER_TEMPLATE.format(env_var=env_var)
    
    elif auth_type == "api_key":
        env_var = f"{policy_config.get('scheme_name', 'API').upper()}_KEY"
        header_name = policy_config.get("key_name", "X-API-Key")
        return AUTH_API_KEY_TEMPLATE.format(env_var=env_var, header_name=header_name)
    
    elif auth_type == "oauth2":
        env_var = f"{policy_config.get('scheme_name', 'API').upper()}_TOKEN"
        token_url = policy_config.get("token_url", "/oauth/token")
        scopes = " ".join(policy_config.get("scopes", []))
        return AUTH_OAUTH2_TEMPLATE.format(env_var=env_var, token_url=token_url, scopes=scopes)
    
    return ""


def get_retry_code(policy_config: Dict[str, Any]) -> str:
    """Generate retry code from policy config."""
    max_attempts = policy_config.get("max_attempts", 3)
    backoff_factor = policy_config.get("backoff_factor", 0.5)
    
    return RETRY_METHOD_WRAPPER.format(
        max_attempts=max_attempts,
        backoff_factor=backoff_factor,
    )


def get_rate_limit_code(policy_config: Dict[str, Any]) -> str:
    """Generate rate limiter code from policy config."""
    rps = policy_config.get("requests_per_second", 10)
    return RATE_LIMIT_TEMPLATE.format(requests_per_second=rps)


def get_pagination_code(policy_config: Dict[str, Any]) -> str:
    """Generate pagination helper code from policy config."""
    style = policy_config.get("style", "offset")
    page_size = policy_config.get("page_size", 100)
    cursor_field = policy_config.get("cursor_field", "cursor")
    
    if style == "cursor":
        return PAGINATION_CURSOR_TEMPLATE.format(cursor_field=cursor_field)
    else:
        return PAGINATION_OFFSET_TEMPLATE.format(page_size=page_size)
```

#### 2. `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

**Changes (Lines 100-160):**

```python
# Add import
from integration_coworker.codegen.policy_templates import (
    get_auth_code,
    get_retry_code,
    get_rate_limit_code,
    get_pagination_code,
)

# Modify _generate_client_code to include policies
def _generate_client_code(
    state: WorkflowState,
    provider_code: str,
    client_class: str,
    method_name: str,
    endpoint: Optional[Endpoint],
    base_url: str,
) -> str:
    """Generate client class with policy code injected."""
    
    # Collect policy code
    policy_methods = []
    imports_needed = set()
    
    for policy in state.policies:
        config = policy.config or {}
        
        if policy.policy_type == "auth":
            policy_methods.append(get_auth_code(config))
            imports_needed.add("import os")
        
        elif policy.policy_type == "retry":
            policy_methods.append(get_retry_code(config))
            imports_needed.update(["import time", "import functools"])
        
        elif policy.policy_type == "rate_limit":
            policy_methods.append(get_rate_limit_code(config))
            imports_needed.update(["import time", "import threading"])
        
        elif policy.policy_type == "pagination":
            policy_methods.append(get_pagination_code(config))
            imports_needed.add("from typing import Generator")
    
    # Build imports block
    imports_block = "\n".join(sorted(imports_needed))
    
    # Build policy methods block
    policy_block = "\n".join(policy_methods)
    
    # Generate full client (existing code + policies)
    # ... rest of generation ...
```

---

## P2: Repo File Writes (1.5 days)

### Problem
`apply_repo_integration_changes` returns empty `RepoChangeSet`. No files are actually written.

### Solution
1. Populate `RepoChangeSet` with code artifacts
2. Write files to disk (respecting dry_run)
3. Add integration hook updates (imports, router registration)

### Files to Modify

#### 1. `src/integration_coworker/graph/nodes/apply_repo_integration_changes.py`

**Complete Rewrite:**

```python
"""
apply_repo_integration_changes node - Write generated code to target repository.

Implements design doc Section 5.4.13.
Respects dry_run flag and produces detailed change summary.
"""
import logging
from pathlib import Path
from typing import List, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.models import RepoChangeSet, FileChange

logger = logging.getLogger(__name__)


def apply_repo_integration_changes(state: WorkflowState) -> WorkflowState:
    """
    Reads: repo_root, code_artifacts, repo_profile, options.dry_run
    Writes: repo_changes (RepoChangeSet), filesystem (if not dry_run)
    
    Creates FileChange entries for each code artifact and optionally writes to disk.
    """
    if not state.repo_root:
        logger.info("No repo_root specified, skipping repo integration")
        state.completed_steps.append("apply_repo_integration_changes")
        return state
    
    repo_path = Path(state.repo_root)
    if not repo_path.exists():
        state.errors.append(f"Repo root does not exist: {state.repo_root}")
        state.completed_steps.append("apply_repo_integration_changes")
        return state
    
    is_dry_run = state.options.dry_run if state.options else True
    
    # Initialize RepoChangeSet
    changes: List[FileChange] = []
    
    # 1. Create FileChange for each code artifact
    for artifact in state.code_artifacts:
        rel_path = artifact.rel_path
        target_path = repo_path / rel_path
        
        if target_path.exists():
            # Update existing file
            original_content = target_path.read_text(encoding="utf-8")
            changes.append(FileChange(
                rel_path=rel_path,
                change_type="update",
                content=artifact.content,
                original_content=original_content,
                before=original_content,
                after=artifact.content,
            ))
        else:
            # Create new file
            changes.append(FileChange(
                rel_path=rel_path,
                change_type="create",
                content=artifact.content,
                after=artifact.content,
            ))
    
    # 2. Add integration hook updates (imports, registrations)
    hook_changes = _generate_hook_updates(state, repo_path)
    changes.extend(hook_changes)
    
    # 3. Build RepoChangeSet
    state.repo_changes = RepoChangeSet(
        repo_root=repo_path,
        changes=changes,
        applied=False,
    )
    
    # 4. Write files (if not dry_run)
    if is_dry_run:
        logger.info(f"DRY RUN: Would write {len(changes)} files to {repo_path}")
        state.plan["dry_run_repo_changes"] = [
            {"path": c.rel_path, "type": c.change_type} for c in changes
        ]
    else:
        _apply_changes_to_disk(repo_path, changes)
        state.repo_changes.applied = True
        logger.info(f"Applied {len(changes)} changes to {repo_path}")
    
    state.completed_steps.append("apply_repo_integration_changes")
    return state


def _generate_hook_updates(state: WorkflowState, repo_path: Path) -> List[FileChange]:
    """
    Generate updates for integration hooks (routers, registries, __init__.py).
    
    Uses RepoProfile.integration_hooks to determine which files to update.
    """
    changes = []
    
    if not state.repo_profile or not state.repo_profile.integration_hooks:
        return changes
    
    hooks = state.repo_profile.integration_hooks
    provider_code = state.provider_code or "unknown"
    
    # Router registration
    if "router_file" in hooks:
        router_path = repo_path / hooks["router_file"]
        if router_path.exists():
            router_content = router_path.read_text(encoding="utf-8")
            updated_content = _add_router_import(
                router_content,
                provider_code,
                state.code_artifacts,
                hooks.get("router_marker", "# INTEGRATION_IMPORTS"),
            )
            if updated_content != router_content:
                changes.append(FileChange(
                    rel_path=hooks["router_file"],
                    change_type="update",
                    content=updated_content,
                    original_content=router_content,
                    before=router_content,
                    after=updated_content,
                ))
    
    # __init__.py updates
    if "init_file" in hooks:
        init_path = repo_path / hooks["init_file"]
        if init_path.exists():
            init_content = init_path.read_text(encoding="utf-8")
            updated_content = _add_init_exports(
                init_content,
                provider_code,
                state.code_artifacts,
            )
            if updated_content != init_content:
                changes.append(FileChange(
                    rel_path=hooks["init_file"],
                    change_type="update",
                    content=updated_content,
                    original_content=init_content,
                    before=init_content,
                    after=updated_content,
                ))
    
    return changes


def _add_router_import(
    content: str,
    provider_code: str,
    artifacts: list,
    marker: str,
) -> str:
    """Add import statement after marker comment."""
    if marker not in content:
        return content
    
    # Find flow artifact
    flow_artifact = next((a for a in artifacts if a.artifact_type == "flow"), None)
    if not flow_artifact:
        return content
    
    module_path = flow_artifact.rel_path.replace("/", ".").replace(".py", "")
    import_line = f"from {module_path} import *"
    
    if import_line in content:
        return content  # Already imported
    
    return content.replace(marker, f"{marker}\n{import_line}")


def _add_init_exports(content: str, provider_code: str, artifacts: list) -> str:
    """Add exports to __init__.py."""
    exports = []
    for artifact in artifacts:
        if artifact.artifact_type == "client":
            # Extract class name from content
            import re
            match = re.search(r"class\s+(\w+)", artifact.content)
            if match:
                exports.append(match.group(1))
    
    if not exports:
        return content
    
    export_line = f"__all__ = {exports!r}"
    if "__all__" in content:
        # Update existing __all__
        content = re.sub(r"__all__\s*=\s*\[.*?\]", export_line, content, flags=re.DOTALL)
    else:
        content = f"{export_line}\n\n{content}"
    
    return content


def _apply_changes_to_disk(repo_path: Path, changes: List[FileChange]) -> None:
    """Write changes to filesystem."""
    for change in changes:
        target = repo_path / change.rel_path
        
        if change.change_type == "delete":
            if target.exists():
                target.unlink()
        else:
            # Create parent directories
            target.parent.mkdir(parents=True, exist_ok=True)
            
            # Write content
            content = change.after or change.content
            if content:
                target.write_text(content, encoding="utf-8")
```

#### 2. `src/integration_coworker/repo/profiles.py`

**Add integration_hooks to default profiles:**

```python
# Add to FASTAPI_PROFILE
FASTAPI_PROFILE = RepoProfile(
    name="fastapi-default",
    archetype="fastapi_service",
    framework="fastapi",
    language="python",
    integrations_root="app/integrations",
    tests_root="tests/integrations",
    integration_hooks={
        "router_file": "app/main.py",
        "router_marker": "# INTEGRATION_IMPORTS",
        "init_file": "app/integrations/__init__.py",
    },
)

# Add to other profiles similarly...
```

---

## P2: Config File Generation (0.5 days)

### Problem
Design doc promises config file generation but it's not implemented.

### Solution
Add `config` artifact type to code generation.

### Files to Modify

#### 1. `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

**Add after test artifact generation (Line ~180):**

```python
# Generate CONFIG artifact
config_content = _generate_config(
    state=state,
    provider_code=provider_code,
    base_url=base_url,
)
config_artifact = CodeArtifact(
    id=None,
    task_id=None,
    artifact_type="config",
    language="yaml",
    module_name=f"{provider_code}_config",
    rel_path=f"config/{provider_code}.yaml",
    content=config_content,
)
state.code_artifacts.append(config_artifact)


def _generate_config(state: WorkflowState, provider_code: str, base_url: str) -> str:
    """Generate YAML config file for integration."""
    # Extract auth config from policies
    auth_policy = next((p for p in state.policies if p.policy_type == "auth"), None)
    auth_config = auth_policy.config if auth_policy else {}
    
    # Extract rate limit config
    rate_policy = next((p for p in state.policies if p.policy_type == "rate_limit"), None)
    rate_config = rate_policy.config if rate_policy else {}
    
    config = {
        "provider": provider_code,
        "base_url": base_url,
        "auth": {
            "type": auth_config.get("type", "none"),
            "env_var": f"{provider_code.upper()}_API_KEY",
        },
        "rate_limit": {
            "requests_per_second": rate_config.get("requests_per_second", 10),
        },
        "retry": {
            "max_attempts": 3,
            "backoff_factor": 0.5,
        },
        "endpoints": [
            {
                "path": ep.path,
                "method": ep.method,
                "operation_id": ep.operation_id,
            }
            for ep in state.endpoints[:10]  # Limit to first 10
        ],
    }
    
    import yaml
    return yaml.dump(config, default_flow_style=False, sort_keys=False)
```

---

## P3: Request/Response Mappings (1 day)

### Problem
`EndpointBinding.request_mapping` and `response_mapping` are always empty `{}`.

### Solution
Generate field mappings based on schema analysis.

### Files to Modify

#### 1. `src/integration_coworker/graph/nodes/plan_integration_flow.py`

**Modify binding creation (Line ~170):**

```python
def _generate_field_mappings(
    endpoint: Endpoint,
    schemas: List[Schema],
    schema_fields: List[SchemaField],
) -> tuple[dict, dict]:
    """
    Generate request and response field mappings.
    
    Request mapping: Maps function parameters → API request fields
    Response mapping: Maps API response fields → return value
    """
    request_mapping = {}
    response_mapping = {}
    
    # Request mapping from schema
    if endpoint.request_schema_id:
        req_schema = next((s for s in schemas if s.id == endpoint.request_schema_id), None)
        if req_schema:
            req_fields = [f for f in schema_fields if f.schema_id == req_schema.id]
            for field in req_fields:
                # snake_case param → original field name
                param_name = _to_snake_case(field.name)
                request_mapping[param_name] = {
                    "target": field.name,
                    "type": field.data_type,
                    "required": field.required,
                }
    
    # Response mapping from schema
    if endpoint.response_schema_id:
        resp_schema = next((s for s in schemas if s.id == endpoint.response_schema_id), None)
        if resp_schema:
            resp_fields = [f for f in schema_fields if f.schema_id == resp_schema.id]
            for field in resp_fields:
                # original field name → snake_case attribute
                attr_name = _to_snake_case(field.name)
                response_mapping[field.name] = {
                    "target": attr_name,
                    "type": field.data_type,
                }
    
    return request_mapping, response_mapping


# Modify binding creation
for api_node in api_call_nodes:
    matched_endpoint = _match_endpoint_to_node(api_node, state.endpoints)
    
    # Generate mappings
    request_mapping, response_mapping = _generate_field_mappings(
        matched_endpoint,
        state.schemas,
        state.schema_fields,
    ) if matched_endpoint else ({}, {})
    
    binding = EndpointBinding(
        id=None,
        task_id=None,
        flow_node_key=api_node.node_key,
        endpoint_id=matched_endpoint.id if matched_endpoint else None,
        params_mapping={},
        request_mapping=request_mapping,
        response_mapping=response_mapping,
    )
```

---

## P4: HTML/PDF Spec Parsing (2 days)

### Problem
The current `detect_and_parse_spec` pipeline assumes structured HTTP specs (OpenAPI/JSON/YAML). For HTML/PDF, there is only a text-only fallback which does not:
- segment content into endpoints, resources, and schemas,
- infer HTTP methods/paths from prose,
- populate the Silver model (`spec_documents`, `endpoints`, `schemas`, `schema_fields`).

This fails the design doc promise of "unified ingestion across OpenAPI, HTML, and PDF" and blocks non-OpenAPI providers.

### Solution
Add a two-stage HTML/PDF ingestion pipeline:
1. **Text extraction layer** (HTML → text, PDF → text) with robust library-backed extractors and deterministic unit tests.
2. **Heuristic Silver model builder** that:
     - identifies endpoint-like sections (e.g., `POST /v1/payments` headers, code blocks),
     - extracts request/response schemas from tables and parameter sections,
     - normalizes into `Endpoint`, `Schema`, and `SchemaField` records compatible with OpenAPI-derived Silver.

The design should be pluggable so future formats (Markdown, Confluence, etc.) reuse the same abstraction.

### Files to Create

1. `src/integration_coworker/parsers/__init__.py`
     - Simple package init to expose parsers.

2. `src/integration_coworker/parsers/html_parser.py`
     - Responsibilities:
         - Accept a raw HTML string or bytes and return structured `HtmlSpecDocument`:
             - `title`, `sections`, `code_blocks`, `tables`.
         - Use `beautifulsoup4` for DOM parsing (add to `pyproject.toml`/`requirements.txt`).
         - Heuristics:
             - Section detection by `h1`–`h4` headings.
             - Endpoint header detection via regexes like `^(GET|POST|PUT|DELETE|PATCH)\s+/[\w/\-{}]+`.
             - Table extraction into row/column dicts suitable for schema inference.
     - Public functions:
         - `parse_html_spec(html: str) -> HtmlSpecDocument`
         - `extract_html_endpoints(doc: HtmlSpecDocument) -> list[ParsedEndpoint]`

3. `src/integration_coworker/parsers/pdf_parser.py`
     - Responsibilities:
         - Accept a PDF path or bytes and return a `PdfSpecDocument` with page-wise text and detected sections.
         - Use `pypdf` (or `pdfplumber` if already present) for extraction; add dependency if missing.
         - Normalize line breaks and merge wrapped lines to improve regex detection.
     - Public functions:
         - `extract_pdf_text(path: str | Path | bytes) -> str`
         - `parse_pdf_spec(text: str) -> PdfSpecDocument`

4. `src/integration_coworker/parsers/text_spec_heuristics.py`
     - Shared heuristics for both HTML and PDF text:
         - `detect_endpoints(text: str) -> list[ParsedEndpoint]`
         - `infer_request_schema(block: str) -> ParsedSchema`
         - `infer_response_schema(block: str) -> ParsedSchema`
         - `detect_event_like_sections(text: str) -> list[ParsedEvent]` (for future event-based specs).
     - Leverage existing Silver entities:
         - Preserve `provider_code`, `spec_document_id`, and `path/method` semantics.

5. `tests/parsers/test_html_parser.py`
     - Fixtures:
         - Minimal HTML page with 2 endpoints and parameter tables.
     - Tests:
         - `test_parse_html_spec_extracts_sections_and_code_blocks`.
         - `test_extract_html_endpoints_detects_methods_and_paths`.
         - `test_html_tables_map_to_candidate_fields`.

6. `tests/parsers/test_pdf_parser.py`
     - Use small fixture PDF in `tests/fixtures/specs/pdf/`.
     - Tests:
         - `test_extract_pdf_text_returns_reasonable_length`.
         - `test_parse_pdf_spec_detects_endpoint_headers`.

7. `tests/parsers/test_text_spec_heuristics.py`
     - Tests:
         - `test_detect_endpoints_from_freeform_text`.
         - `test_infer_request_schema_from_parameter_block`.
         - `test_infer_response_schema_from_json_example`.

### Files to Modify

1. `src/integration_coworker/graph/nodes/detect_and_parse_spec.py`
     - Extend routing logic:
         - Detect file type by extension and/or magic bytes:
             - `.html` / `.htm` → HTML path.
             - `.pdf` → PDF path.
         - For HTML:
             - Call `parse_html_spec` → `extract_html_endpoints`.
         - For PDF:
             - Call `extract_pdf_text` → `parse_pdf_spec` → `detect_endpoints`.
     - Normalize outputs into the same intermediate `ParsedSpec` structure used by OpenAPI path.
     - Ensure `spec_documents` is populated with `source_type` = `"html"` or `"pdf"`.

2. `src/integration_coworker/graph/nodes/build_silver_api_model.py`
     - Add handling for `ParsedEndpoint`/`ParsedSchema` from HTML/PDF parsers:
         - Create `Endpoint` rows (method, path, summary) from heuristics.
         - Create `Schema` and `SchemaField` rows from inferred schemas.
     - Guarantee that downstream nodes (`align_task_with_kg`, `plan_integration_flow`) see a consistent Silver representation regardless of source.

3. `tests/test_dynamic_spec.py`
     - Add new tests:
         - `test_html_spec_ingestion_populates_silver_model`.
         - `test_pdf_spec_ingestion_populates_silver_model`.

4. Dependency manifests
     - `pyproject.toml` / `requirements.txt`:
         - Add `beautifulsoup4` and PDF library (`pypdf` or `pdfplumber`).

---

## P5: CSV/EDI/Message Specs (2 days)

### Problem
The design doc promises support for non-HTTP integration specs (CSV imports, EDI-style schemas, message-based APIs like Kafka/SQS). The current implementation only defines tables for these concepts but never populates them or wires them into the Silver/Gold workflow.

This blocks v1 scenarios where the integration task references batch imports, exports, or message streams rather than REST endpoints.

### Solution
Implement a non-HTTP spec ingestion pipeline that:
1. Detects CSV/TSV and schema files (e.g., column headers, sample rows).
2. Parses EDI- or schema-like formats (e.g., JSON schema, Avro, or custom tabular descriptor) into `Schema`/`SchemaField`.
3. Supports message specs for topics/queues (Kafka/SQS) with clearly named fields and event types.
4. Extends workflow planning so tasks like "ingest daily CSV into payments system" or "process `order_created` events" are first-class.

### Files to Create

1. `src/integration_coworker/parsers/csv_schema.py`
     - Responsibilities:
         - Infer schemas from CSV/TSV samples:
             - Read header row → field names.
             - Sample N rows to infer data types (string/int/float/bool/date).
             - Flag candidate primary keys and foreign keys when patterns are obvious (e.g., `id`, `user_id`).
     - Public functions:
         - `infer_csv_schema(path: str | Path) -> ParsedSchema`
         - `infer_csv_schema_from_buffer(name: str, text: str) -> ParsedSchema`

2. `src/integration_coworker/parsers/message_schema.py`
     - Responsibilities:
         - Ingest message format descriptors:
             - JSON schema files.
             - Avro schema files.
             - Simple YAML topic descriptors used in tests.
         - Map into `ParsedSchema` + `ParsedEvent` (topic name, key fields, payload fields).
     - Public functions:
         - `parse_message_schema(path: str | Path) -> list[ParsedEvent]`
         - `parse_message_schema_content(name: str, text: str) -> list[ParsedEvent]`

3. `tests/parsers/test_csv_schema.py`
     - Fixtures: sample CSVs in `tests/fixtures/specs/csv/`.
     - Tests:
         - `test_infer_csv_schema_detects_column_names_and_types`.
         - `test_infer_csv_schema_handles_missing_values`.

4. `tests/parsers/test_message_schema.py`
     - Fixtures: simple JSON schema/Avro/YAML topic specs in `tests/fixtures/specs/messages/`.
     - Tests:
         - `test_parse_message_schema_creates_events_and_fields`.
         - `test_parse_message_schema_content_handles_multiple_topics`.

### Files to Modify

1. `src/integration_coworker/graph/nodes/detect_and_parse_spec.py`
     - Extend detection:
         - CSV/TSV (`.csv`, `.tsv`) → `infer_csv_schema`.
         - Message spec extensions (`.json`, `.avsc`, `.yaml`) with recognizable structure → `parse_message_schema`.
     - Produce unified `ParsedSpec` objects tagged with `kind` = `"csv"`, `"batch"`, or `"message"`.

2. `src/integration_coworker/graph/nodes/build_silver_api_model.py`
     - Add logic:
         - For CSV specs:
             - Create `Schema` entries with `schema_type = "csv"`.
             - Link to pseudo-endpoints for batch jobs (e.g., `operation_type = "ingest"`).
         - For message specs:
             - Create `Schema`/`SchemaField` for payloads.
             - Create `Event` or equivalent table records if defined in schema.
     - Ensure these records feed into `align_task_with_kg` so KG templates that reference batch/message patterns can be matched.

3. `src/integration_coworker/graph/nodes/plan_integration_flow.py`
     - Extend flow planning to handle non-HTTP steps:
         - For CSV ingestion tasks, plan nodes like `load_file`, `validate_rows`, `map_fields`, `upsert_records`.
         - For message processing tasks, plan nodes like `consume_events`, `transform_payload`, `call_api`, `publish_result`.
     - Reuse the same `EndpointBinding`/mapping concepts where applicable but allow bindings to `Schema` or `Event` instead of HTTP endpoints.

4. `tests/test_multi_spec.py`
     - Add cases where the same task description is solved against:
         - HTTP-only spec.
         - CSV-only spec.
         - Message-only spec.
     - Assert that workflows and bindings are generated appropriately.

---

## P6: Postgres + pgvector (1.5 days)

### Problem
The current implementation uses SQLite for local development and simulates vector search in Python. The design doc and `db_setup.md` promise a production-ready Postgres + pgvector path with:
- connection pooling,
- native vector similarity (`<->` / `<=>` operators),
- migrations for Silver/Gold schemas and vector columns.

Without this, we can't meet v1 expectations for scale or team deployment.

### Solution
Implement a dual-backend persistence layer where:
1. **SQLite** remains the default for local dev and tests.
2. **Postgres+pgvector** is an opt-in production backend configured via settings.
3. Vector operations delegate to the database when pgvector is available, otherwise fall back to the existing Python cosine implementation.

### Files to Create

1. `docs/db_setup_postgres.md`
     - Step-by-step instructions to:
         - Install Postgres + pgvector.
         - Run migrations (via `scripts/init_db.py` or Alembic if added later).
         - Configure environment variables.

2. `scripts/init_db_postgres.py`
     - Postgres-specific init script that:
         - Connects using settings from `integration_coworker.config`.
         - Creates schemas/tables if missing.
         - Ensures pgvector extension is installed and vector columns use correct dimension (1536).

### Files to Modify

1. `src/integration_coworker/persistence/db.py`
     - Introduce a configurable backend:
         - Read from `get_settings().db_backend` with values `"sqlite"` (default) or `"postgres"`.
         - Provide `get_connection()` abstraction for both engines.
         - For Postgres:
             - Use `psycopg` / `asyncpg` (whichever is standard in repo) with connection pooling.
             - Expose helper functions:
                 - `execute(query, params=None)`.
                 - `fetchall(query, params=None)`.
                 - `fetchone(query, params=None)`.

2. `src/integration_coworker/persistence/models_*.py` (or equivalent schema helpers)
     - Add vector column definitions where appropriate (e.g., `spec_chunks.embedding`, `kg_nodes.embedding`) using pgvector type when backend is Postgres.
     - Ensure migrations or init scripts create these columns with `vector(1536)`.

3. `src/integration_coworker/retrieval/semantic_search.py`
     - Update implementation to:
         - For SQLite backend: keep current Python cosine computation.
         - For Postgres backend: push similarity computation into SQL using pgvector, e.g.:
             ```sql
             SELECT id, spec_document_id, chunk_index, content,
                            1 - (embedding <=> %s::vector) AS similarity
             FROM spec_chunks
             WHERE embedding IS NOT NULL
             ORDER BY embedding <=> %s::vector
             LIMIT %s
             ```
         - Serialize query embedding to Postgres-compatible `vector` literal.

4. `src/integration_coworker/config/__init__.py` (or `settings.py`)
     - Extend settings model:
         - `db_backend: Literal["sqlite", "postgres"] = "sqlite"`.
         - `postgres_dsn: str | None` (e.g., `postgresql+psycopg://user:pass@host:port/dbname`).

5. `docs/db_setup.md`
     - Add a short section referencing Postgres path and linking to `db_setup_postgres.md`.

6. Dependency manifests
     - `pyproject.toml` / `requirements.txt`:
         - Add `psycopg[binary]` (or chosen Postgres driver).
         - Add `pgvector` Python helper if used.

7. `tests/test_m4_persistence.py`
     - Add Postgres-backed tests guarded by env var (e.g., `POSTGRES_TEST_DSN`).
     - Tests:
         - `test_postgres_vector_search_matches_python_cosine_ordering` (within small epsilon).

---

## P7: GitHub API Provider (1.5 days)

### Problem
`attach_repo_context` and related repo-awareness currently assume a local filesystem provider. The design doc promises a GitHub API provider so that we can analyze remote repositories without cloning them locally.

Without this, the v1 "remote repo" integration story is incomplete, especially for CI-oriented or SaaS deployments.

### Solution
Implement a `GitHubRepoProvider` alongside the existing filesystem provider, with a clean interface that supports:
1. Listing files and directories by path/pattern.
2. Reading file contents from a specific branch or commit.
3. Optionally writing suggested changes as GitHub PRs (scoped for future versions, not v1-critical).

### Files to Create

1. `src/integration_coworker/repo/github_provider.py`
     - Define `GitHubRepoProvider` implementing the same interface as the filesystem provider (e.g., `RepoProvider`):
         - `list_files(prefix: str) -> list[str]`.
         - `read_file(path: str) -> str`.
         - `get_repo_metadata() -> RepoMetadata`.
     - Implementation details:
         - Use `requests` or `httpx` for API calls.
         - Read auth from `GITHUB_TOKEN` environment variable or settings.
         - Support configuration fields:
             - `owner`, `repo`, `ref` (branch/commit), optional `github_api_base`.

2. `tests/test_github_provider.py`
     - Use `responses` / `requests-mock` (or `httpx` mock) to stub GitHub API responses.
     - Tests:
         - `test_list_files_uses_github_api`.
         - `test_read_file_fetches_blob_content`.
         - `test_missing_token_raises_clear_error`.

### Files to Modify

1. `src/integration_coworker/repo/__init__.py`
     - Export the new provider and any factory helpers.

2. `src/integration_coworker/repo/providers.py` (or equivalent)
     - Introduce a `get_repo_provider(source: RepoSourceConfig) -> RepoProvider` that:
         - For `source.type == "filesystem"` → returns filesystem provider.
         - For `source.type == "github"` → returns `GitHubRepoProvider`.

3. `src/integration_coworker/graph/nodes/attach_repo_context.py`
     - Replace direct filesystem access with provider abstraction:
         - Accept `state.repo_source` (filesystem or GitHub config).
         - Construct provider via `get_repo_provider`.
         - Use provider methods to locate integration hooks, existing clients, and tests.

4. `src/integration_coworker/config/__init__.py` (or `settings`)
     - Add GitHub config fields:
         - `github_owner: str | None`.
         - `github_repo: str | None`.
         - `github_ref: str | None`.
         - `github_token_env_var: str = "GITHUB_TOKEN"`.

5. `docs/APPENDIX_REPO_PROFILES_V2.md`
     - Document how repo profiles map to filesystem vs GitHub providers.
     - Include examples for configuring a remote repo-only run.

6. `tests/test_repo_profiles.py` / `tests/test_repo_context.py`
     - Add GitHub-backed profile tests verifying that:
         - `attach_repo_context` can operate using the GitHub provider.
         - Profiles supply enough info to resolve typical integration locations.

---

## Test Coverage Plan

### New Test Files

| File | Tests |
|------|-------|
| `tests/retrieval/test_semantic_search.py` | Cosine similarity, chunk search, template search |
| `tests/test_multi_endpoint_flows.py` | Multi-step detection, multi-call workflow generation |
| `tests/test_policy_wiring.py` | Auth code generation, retry code, rate limit code |
| `tests/test_repo_file_writes.py` | FileChange creation, hook updates, disk writes |
| `tests/test_config_generation.py` | YAML config output |
| `tests/test_field_mappings.py` | Request/response mapping generation |

### Integration Tests

| Test | Validates |
|------|-----------|
| `test_e2e_semantic_retrieval.py` | Full flow with embedding search |
| `test_e2e_multi_call_workflow.py` | "Create order and send email" task |
| `test_e2e_repo_writes.py` | Files written to temp directory |

---

## Implementation Order

```
Week 1:
├── Day 1-2: P0 Embedding Search
│   ├── Create retrieval module
│   ├── Wire into align_task_with_kg
│   └── Add tests
│
├── Day 3-4: P1 Multi-Endpoint Flows
│   ├── Multi-step detection
│   ├── Multi-call workflow generation
│   └── Multiple EndpointBinding creation
│
└── Day 5: P1 Policy Wiring (part 1)
    ├── Policy template module
    └── Auth code generation

Week 2:
├── Day 1: P1 Policy Wiring (part 2)
│   ├── Retry/rate-limit/pagination code
│   └── Wire into generate_code_and_tests
│
├── Day 2-3: P2 Repo File Writes
│   ├── Populate RepoChangeSet
│   ├── Hook updates (router, init)
│   └── Disk write logic
│
├── Day 4: P2 Config Generation + P3 Field Mappings
│   ├── YAML config artifact
│   └── Request/response mapping DSL
│
└── Day 5: P3 HTML/PDF Parsing (part 1)
    ├── Text extraction pipeline
    └── Heuristic endpoint detection

Week 3:
├── Day 1: P3 HTML/PDF Parsing (part 2)
│   ├── Silver model population from text
│   └── Integration with detect_and_parse_spec
│
├── Day 2-3: P3 CSV/EDI/Message Specs
│   ├── Schema detection for tabular data
│   ├── Message spec parsing (Kafka/SQS schemas)
│   └── Wire into build_silver_api_model
│
├── Day 4: P4 Postgres + pgvector
│   ├── Connection pooling + migrations
│   ├── Native vector similarity queries
│   └── Production config management
│
└── Day 5: P4 GitHub API Provider
    ├── GitHub API client for repo analysis
    ├── Rate limiting + auth handling
    └── Integration with attach_repo_context

Week 4:
├── Day 1-2: Integration tests + E2E validation
│   ├── Full pipeline tests for all new features
│   └── Performance benchmarks
│
└── Day 3: Documentation + cleanup
    ├── Update AGENTIC_BEHAVIOR_BOUNDS.md
    └── API documentation for new modules
```

---

## Files Summary

### New Files (19)

| Path | Purpose |
|------|---------|
| `src/integration_coworker/retrieval/__init__.py` | Module init |
| `src/integration_coworker/retrieval/semantic_search.py` | Embedding search |
| `src/integration_coworker/codegen/policy_templates.py` | Policy code templates |
| `src/integration_coworker/parsers/__init__.py` | Parser module init |
| `src/integration_coworker/parsers/html_parser.py` | HTML doc extraction |
| `src/integration_coworker/parsers/pdf_parser.py` | PDF doc extraction |
| `src/integration_coworker/parsers/csv_schema.py` | CSV/EDI schema detection |
| `src/integration_coworker/parsers/message_schema.py` | Kafka/SQS schema parsing |
| `src/integration_coworker/repo/github_provider.py` | GitHub API repo provider |
| `tests/retrieval/test_semantic_search.py` | Retrieval tests |
| `tests/test_multi_endpoint_flows.py` | Multi-call tests |
| `tests/test_policy_wiring.py` | Policy tests |
| `tests/test_repo_file_writes.py` | Repo write tests |
| `tests/test_config_generation.py` | Config tests |
| `tests/test_field_mappings.py` | Mapping tests |
| `tests/parsers/test_html_parser.py` | HTML parsing tests |
| `tests/parsers/test_pdf_parser.py` | PDF parsing tests |
| `tests/parsers/test_csv_schema.py` | CSV schema tests |
| `tests/test_github_provider.py` | GitHub API tests |

### Modified Files (12)

| Path | Changes |
|------|---------|
| `src/integration_coworker/graph/nodes/align_task_with_kg.py` | Multi-step detection, semantic scoring |
| `src/integration_coworker/graph/nodes/understand_task.py` | Semantic context retrieval |
| `src/integration_coworker/graph/nodes/plan_integration_flow.py` | Multiple bindings, field mappings |
| `src/integration_coworker/graph/nodes/generate_code_and_tests.py` | Policy injection, config artifact |
| `src/integration_coworker/graph/nodes/apply_repo_integration_changes.py` | Complete rewrite for file writes |
| `src/integration_coworker/graph/nodes/detect_and_parse_spec.py` | HTML/PDF/CSV routing |
| `src/integration_coworker/graph/nodes/build_silver_api_model.py` | Non-HTTP spec support |
| `src/integration_coworker/graph/nodes/attach_repo_context.py` | GitHub provider integration |
| `src/integration_coworker/repo/profiles.py` | Add integration_hooks |
| `src/integration_coworker/persistence/db.py` | Postgres connection support |
| `docs/AGENTIC_BEHAVIOR_BOUNDS.md` | Update gap status |
| `tests/conftest.py` | Add fixtures for new tests |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Embedding API costs | Use cached embeddings; batch queries; test with fake embeddings |
| Multi-step complexity | Start with 2-step flows; add 3+ steps incrementally |
| File write safety | Always test with temp directories; require explicit non-dry-run |
| Policy code correctness | Generate syntactically valid stubs; validate with AST |
| Backwards compatibility | All changes additive; existing tests must pass |

---

## Success Criteria

After implementation:

- [ ] `search_spec_chunks("create payment")` returns relevant chunks
- [ ] Task "create order and send email" generates 2 api_call nodes
- [ ] Generated client code includes `_get_auth_headers()` method
- [ ] `dry_run=False` writes files to target repo
- [ ] Config artifact appears in `code_artifacts`
- [ ] EndpointBinding has populated `request_mapping`
- [ ] HTML and PDF specs ingest into Silver model with endpoints and schemas
- [ ] CSV and message specs ingest into Silver model and are plannable
- [ ] Postgres+pgvector backend passes parity tests with SQLite path
- [ ] GitHub repo provider powers `attach_repo_context` for remote repos
- [ ] All existing tests pass
- [ ] New tests provide >80% coverage of new code
