"""
Persist KG Learning Node

Per design doc Section 5.4 and Appendix H.4:
This node persists knowledge graph facts learned from each run.

Runs after persist_gold_checkpoint and:
- Creates/upserts KG nodes for tasks, workflows, entities, endpoints
- Creates/upserts KG edges for relationships between nodes
- Optionally computes embeddings for workflow templates
- Respects dry_run: skips writes if dry_run=True

KG Schema:
- kg.nodes: Core graph nodes (provider, entity, endpoint, workflow_template, task)
- kg.edges: Relationships between nodes
- kg.workflow_steps: Steps within workflow templates
- kg.step_bindings: Endpoint bindings for steps

V2.1: Uses LangChain OpenAIEmbeddings for automatic LangSmith tracing.
"""
import json
import logging
import re
from typing import Optional, List, Dict, Any, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence import db
from integration_coworker.config import get_settings
from integration_coworker.domain.models import KGNodeType, KGEdgeRelation
from integration_coworker.kg import STANDARD_PATTERNS

logger = logging.getLogger(__name__)


# Import LangChain embeddings for LangSmith tracing
try:
    from langchain_openai import OpenAIEmbeddings
    HAS_LANGCHAIN_EMBEDDINGS = True
except ImportError:
    HAS_LANGCHAIN_EMBEDDINGS = False
    OpenAIEmbeddings = None


def _get_embedding_client():
    """
    Get LangChain OpenAIEmbeddings client for automatic LangSmith tracing.
    
    Returns an embeddings client or None if unavailable.
    """
    if not HAS_LANGCHAIN_EMBEDDINGS:
        return None
    
    settings = get_settings()
    if not settings.llm.api_key or settings.llm.use_mock:
        return None
    
    try:
        model = settings.llm.embedding_model or "text-embedding-3-small"
        return OpenAIEmbeddings(
            api_key=settings.llm.api_key,
            model=model,
        )
    except Exception as e:
        logger.warning(f"Failed to create LangChain OpenAIEmbeddings client: {e}")
        return None


def _compute_embedding(text: str) -> Optional[List[float]]:
    """
    Compute embedding for text using LangChain OpenAIEmbeddings.
    
    Uses LangChain for automatic LangSmith tracing of embedding calls.
    """
    settings = get_settings()
    if not settings.llm.api_key or settings.llm.use_mock:
        logger.debug("Embedding computation skipped: mock LLM or no API key")
        return None

    client = _get_embedding_client()
    if not client:
        logger.debug("Embedding client unavailable")
        return None

    try:
        # Use LangChain's embed_query for single text (traced in LangSmith)
        truncated_text = text[:8000]  # Truncate to avoid token limits
        return client.embed_query(truncated_text)
    except Exception as e:
        logger.warning(f"Failed to compute embedding: {e}")
        return None


# Track whether we've logged embedding degradation for this run
_logged_embedding_warning = False


def _detect_pattern_from_workflow(
    workflow_nodes: List,
    endpoints: List,
    task_description: str,
) -> Tuple[Optional[str], float]:
    """
    Detect which STANDARD_PATTERN a workflow most closely matches.
    
    Matching is based on:
    1. HTTP methods of bound endpoints (POST -> create, GET -> list/read, etc.)
    2. Step types in the workflow (api_call, validation, pagination, etc.)
    3. Task description keywords
    
    Returns:
        Tuple of (pattern_key, confidence) or (None, 0.0) if no match
    """
    if not workflow_nodes:
        return None, 0.0
    
    # Extract HTTP methods from endpoints
    http_methods = set()
    for ep in endpoints:
        if hasattr(ep, 'method') and ep.method:
            http_methods.add(ep.method.upper())
    
    # Extract step types from workflow
    step_types = [n.node_type for n in workflow_nodes if hasattr(n, 'node_type')]
    has_validation = "validation" in step_types
    has_pagination = "pagination" in step_types
    has_api_call = "api_call" in step_types
    api_call_count = step_types.count("api_call")
    
    # Check endpoint paths for patterns
    endpoint_paths = [ep.path for ep in endpoints if hasattr(ep, 'path')]
    has_id_param = any('{' in path and '}' in path for path in endpoint_paths)
    
    task_lower = task_description.lower() if task_description else ""
    
    best_match = None
    best_confidence = 0.0
    
    for pattern_key, pattern in STANDARD_PATTERNS.items():
        confidence = 0.0
        
        # Check HTTP method match (strong signal)
        pattern_methods = set(pattern.get("http_methods", []))
        if pattern_methods and http_methods:
            method_overlap = len(http_methods & pattern_methods) / len(pattern_methods)
            confidence += method_overlap * 0.4
        
        # Check path pattern match
        if "path_pattern" in pattern and endpoint_paths:
            pattern_regex = pattern["path_pattern"]
            for path in endpoint_paths:
                if re.match(pattern_regex, path):
                    confidence += 0.2
                    break
        
        # Check pattern-specific indicators
        if pattern_key == "crud_create" and "POST" in http_methods and has_validation:
            confidence += 0.2
        elif pattern_key == "crud_list" and "GET" in http_methods and has_pagination:
            confidence += 0.2
        elif pattern_key == "crud_read" and "GET" in http_methods and has_id_param:
            confidence += 0.15
        elif pattern_key == "crud_update" and ("PUT" in http_methods or "PATCH" in http_methods):
            confidence += 0.2
        elif pattern_key == "crud_delete" and "DELETE" in http_methods:
            confidence += 0.2
        elif pattern_key == "nested_resource" and api_call_count > 1:
            confidence += 0.15
        
        # Check keywords in task description
        keywords = pattern.get("keywords", [])
        if not keywords:
            # Default keywords from pattern key
            keywords = pattern_key.replace("_", " ").split()
        for kw in keywords:
            if kw.lower() in task_lower:
                confidence += 0.1
                break
        
        if confidence > best_confidence:
            best_confidence = confidence
            best_match = pattern_key
    
    # Only return a match if confidence is above threshold
    if best_confidence >= 0.3:
        return best_match, best_confidence
    return None, 0.0


def _upsert_kg_node(
    cur,
    node_type: str,
    key: str,
    name: str,
    provider_code: Optional[str],
    description: Optional[str] = None,
    properties: Optional[Dict[str, Any]] = None,
    embedding: Optional[List[float]] = None,
    run_id: Optional[str] = None,
    is_postgres: bool = False,
) -> int:
    """Upsert a KG node and return its ID."""
    props_json = json.dumps(properties or {})
    embedding_val = json.dumps(embedding) if embedding and not is_postgres else embedding

    if is_postgres:
        # Postgres with pgvector
        if embedding:
            cur.execute("""
                INSERT INTO kg.nodes (node_type, key, name, provider_code, description, properties, embedding, source_run_id, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (node_type, key) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = COALESCE(EXCLUDED.description, kg.nodes.description),
                    properties = EXCLUDED.properties,
                    embedding = COALESCE(EXCLUDED.embedding, kg.nodes.embedding),
                    usage_count = kg.nodes.usage_count + 1,
                    last_used_at = NOW(),
                    source_run_id = EXCLUDED.source_run_id,
                    updated_at = NOW()
                RETURNING id
            """, (node_type, key, name, provider_code, description, props_json, embedding, run_id))
        else:
            cur.execute("""
                INSERT INTO kg.nodes (node_type, key, name, provider_code, description, properties, source_run_id, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (node_type, key) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = COALESCE(EXCLUDED.description, kg.nodes.description),
                    properties = EXCLUDED.properties,
                    usage_count = kg.nodes.usage_count + 1,
                    last_used_at = NOW(),
                    source_run_id = EXCLUDED.source_run_id,
                    updated_at = NOW()
                RETURNING id
            """, (node_type, key, name, provider_code, description, props_json, run_id))
        row = cur.fetchone()
        return row['id'] if isinstance(row, dict) else row[0]
    else:
        # SQLite
        cur.execute("""
            INSERT INTO kg_nodes (node_type, key, name, provider_code, description, properties, embedding, source_run_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(node_type, key) DO UPDATE SET
                name = excluded.name,
                description = COALESCE(excluded.description, kg_nodes.description),
                properties = excluded.properties,
                embedding = COALESCE(excluded.embedding, kg_nodes.embedding),
                usage_count = kg_nodes.usage_count + 1,
                last_used_at = datetime('now'),
                source_run_id = excluded.source_run_id,
                updated_at = datetime('now')
        """, (node_type, key, name, provider_code, description, props_json, embedding_val, run_id))
        cur.execute("SELECT id FROM kg_nodes WHERE node_type = ? AND key = ?", (node_type, key))
        return cur.fetchone()[0]


def _upsert_kg_edge(
    cur,
    src_node_id: int,
    dst_node_id: int,
    relation_type: str,
    weight: float = 1.0,
    properties: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    is_postgres: bool = False,
) -> int:
    """Upsert a KG edge and return its ID."""
    props_json = json.dumps(properties or {})

    if is_postgres:
        cur.execute("""
            INSERT INTO kg.edges (src_node_id, dst_node_id, relation_type, weight, properties, source_run_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (src_node_id, dst_node_id, relation_type) DO UPDATE SET
                weight = kg.edges.weight + 0.1,
                properties = EXCLUDED.properties,
                source_run_id = EXCLUDED.source_run_id
            RETURNING id
        """, (src_node_id, dst_node_id, relation_type, weight, props_json, run_id))
        row = cur.fetchone()
        return row['id'] if isinstance(row, dict) else row[0]
    else:
        cur.execute("""
            INSERT INTO kg_edges (src_node_id, dst_node_id, relation_type, weight, properties, source_run_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(src_node_id, dst_node_id, relation_type) DO UPDATE SET
                weight = kg_edges.weight + 0.1,
                properties = excluded.properties,
                source_run_id = excluded.source_run_id
        """, (src_node_id, dst_node_id, relation_type, weight, props_json, run_id))
        cur.execute("""
            SELECT id FROM kg_edges WHERE src_node_id = ? AND dst_node_id = ? AND relation_type = ?
        """, (src_node_id, dst_node_id, relation_type))
        return cur.fetchone()[0]


def _upsert_workflow_step(
    cur,
    template_node_id: int,
    step_key: str,
    step_type: str,
    position: int,
    label: Optional[str] = None,
    description: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    is_postgres: bool = False,
) -> int:
    """Upsert a workflow step and return its ID."""
    config_json = json.dumps(config or {})

    if is_postgres:
        cur.execute("""
            INSERT INTO kg.workflow_steps (template_node_id, step_key, step_type, position, label, description, config)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (template_node_id, step_key) DO UPDATE SET
                step_type = EXCLUDED.step_type,
                position = EXCLUDED.position,
                label = EXCLUDED.label,
                description = EXCLUDED.description,
                config = EXCLUDED.config
            RETURNING id
        """, (template_node_id, step_key, step_type, position, label, description, config_json))
        row = cur.fetchone()
        return row['id'] if isinstance(row, dict) else row[0]
    else:
        cur.execute("""
            INSERT INTO kg_workflow_steps (template_node_id, step_key, step_type, position, label, description, config)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(template_node_id, step_key) DO UPDATE SET
                step_type = excluded.step_type,
                position = excluded.position,
                label = excluded.label,
                description = excluded.description,
                config = excluded.config
        """, (template_node_id, step_key, step_type, position, label, description, config_json))
        cur.execute("""
            SELECT id FROM kg_workflow_steps WHERE template_node_id = ? AND step_key = ?
        """, (template_node_id, step_key))
        return cur.fetchone()[0]


def _upsert_step_binding(
    cur,
    step_id: int,
    endpoint_node_id: Optional[int],
    endpoint_path: Optional[str],
    endpoint_method: Optional[str],
    request_mapping: Optional[Dict[str, Any]] = None,
    response_mapping: Optional[Dict[str, Any]] = None,
    is_postgres: bool = False,
) -> int:
    """
    Create or update a step→endpoint binding in kg.step_bindings.
    
    KG-001 Fix: This function populates the step_bindings table which links
    workflow steps to their bound endpoints, enabling GraphRAG to recommend
    specific endpoints for specific workflow steps.
    
    Args:
        cur: Database cursor
        step_id: ID of the workflow step (from kg.workflow_steps)
        endpoint_node_id: ID of the endpoint node (from kg.nodes WHERE node_type='endpoint')
        endpoint_path: The endpoint path (e.g., "/v1/checkout/sessions")
        endpoint_method: The HTTP method (e.g., "POST")
        request_mapping: Request parameter mapping dict
        response_mapping: Response field mapping dict
        is_postgres: Whether using Postgres or SQLite
        
    Returns:
        The ID of the created/updated step_binding
    """
    request_json = json.dumps(request_mapping or {})
    response_json = json.dumps(response_mapping or {})
    
    if is_postgres:
        cur.execute("""
            INSERT INTO kg.step_bindings (
                step_id, endpoint_node_id, endpoint_path, endpoint_method,
                request_mapping, response_mapping, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (step_id, endpoint_node_id) DO UPDATE SET
                endpoint_path = EXCLUDED.endpoint_path,
                endpoint_method = EXCLUDED.endpoint_method,
                request_mapping = EXCLUDED.request_mapping,
                response_mapping = EXCLUDED.response_mapping
            RETURNING id
        """, (step_id, endpoint_node_id, endpoint_path, endpoint_method, request_json, response_json))
        row = cur.fetchone()
        return row['id'] if isinstance(row, dict) else row[0]
    else:
        cur.execute("""
            INSERT INTO kg_step_bindings (
                step_id, endpoint_node_id, endpoint_path, endpoint_method,
                request_mapping, response_mapping, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(step_id, endpoint_node_id) DO UPDATE SET
                endpoint_path = excluded.endpoint_path,
                endpoint_method = excluded.endpoint_method,
                request_mapping = excluded.request_mapping,
                response_mapping = excluded.response_mapping
        """, (step_id, endpoint_node_id, endpoint_path, endpoint_method, request_json, response_json))
        cur.execute("""
            SELECT id FROM kg_step_bindings WHERE step_id = ? AND endpoint_node_id = ?
        """, (step_id, endpoint_node_id))
        return cur.fetchone()[0]


def _increment_usage_count(
    cur,
    node_key: str,
    is_postgres: bool = False,
) -> bool:
    """
    V1.1 FT-011: Increment usage_count for a node by key.
    
    Used to track how often templates and patterns are successfully used.
    
    Args:
        cur: Database cursor
        node_key: The key of the node to update
        is_postgres: Whether using Postgres or SQLite
        
    Returns:
        True if node was found and updated, False otherwise
    """
    try:
        if is_postgres:
            cur.execute("""
                UPDATE kg.nodes
                SET usage_count = usage_count + 1,
                    last_used_at = NOW()
                WHERE key = %s
                RETURNING id
            """, (node_key,))
            result = cur.fetchone()
            return result is not None
        else:
            cur.execute("""
                UPDATE kg_nodes
                SET usage_count = usage_count + 1,
                    last_used_at = datetime('now')
                WHERE key = ?
            """, (node_key,))
            return cur.rowcount > 0
    except Exception as e:
        logger.debug(f"Failed to increment usage count for {node_key}: {e}")
        return False


def persist_kg_learning(state: WorkflowState) -> WorkflowState:
    """
    Persist knowledge graph facts learned from this run.
    
    Per design doc Appendix H.4:
    - Runs after persist_gold_checkpoint
    - Creates/updates KG nodes for tasks, templates, entities, endpoints
    - Creates edges expressing relationships
    - Respects dry_run flag
    
    Reads: provider_code, integration_task, workflow_nodes, workflow_edges,
           endpoints, entities, persisted_ids
    Writes: kg.nodes, kg.edges, kg.workflow_steps
    """
    global _logged_embedding_warning

    is_dry_run = state.options.dry_run if state.options else False

    if is_dry_run:
        logger.info("persist_kg_learning: Dry run - skipping KG writes")
        state.persisted_ids.update({
            "kg_learning_dry_run": True,
            "would_persist_kg": {
                "nodes": "task + template + entities + endpoints",
                "edges": "task→template, task→endpoints, task→entities",
                "workflow_steps": len(state.workflow_nodes),
            },
        })
        state.completed_steps.append("persist_kg_learning")
        return state

    if not state.provider_code:
        logger.warning("No provider_code, skipping KG learning")
        state.completed_steps.append("persist_kg_learning")
        return state

    # Log embedding availability once per run
    settings = get_settings()
    embeddings_available = bool(settings.llm.api_key and not settings.llm.use_mock)
    if not embeddings_available and not _logged_embedding_warning:
        logger.info(
            "persist_kg_learning: Embeddings NOT available (mock LLM or no API key). "
            "KG nodes will be created without embeddings - GraphRAG scoring will be degraded."
        )
        _logged_embedding_warning = True
    elif embeddings_available:
        logger.debug("persist_kg_learning: Embeddings available for KG nodes")

    try:
        db.init_schema()
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()

        provider_code = state.provider_code
        run_id = state.run_id
        task_slug = state.integration_task.task_slug if state.integration_task else None
        task_description = state.integration_task.description if state.integration_task else state.task_description

        # =====================================================================
        # 1. Create/upsert provider node
        # =====================================================================
        provider_key = f"provider.{provider_code}"
        provider_node_id = _upsert_kg_node(
            cur,
            node_type=KGNodeType.PROVIDER.value,
            key=provider_key,
            name=provider_code.replace("_", " ").title(),
            provider_code=provider_code,
            description=f"API provider: {provider_code}",
            run_id=run_id,
            is_postgres=is_postgres,
        )

        # =====================================================================
        # 2. Create/upsert task node (the current integration task)
        # =====================================================================
        task_node_id = None
        if task_slug:
            task_key = f"task.{provider_code}.{task_slug}"
            # Compute embedding for task description for semantic search
            task_embedding = _compute_embedding(task_description) if task_description else None

            task_node_id = _upsert_kg_node(
                cur,
                node_type=KGNodeType.TASK.value,
                key=task_key,
                name=task_slug.replace("_", " ").title(),
                provider_code=provider_code,
                description=task_description,
                embedding=task_embedding,
                run_id=run_id,
                is_postgres=is_postgres,
            )

            # Edge: task → provider
            _upsert_kg_edge(
                cur,
                src_node_id=task_node_id,
                dst_node_id=provider_node_id,
                relation_type=KGEdgeRelation.BELONGS_TO_PROVIDER.value,
                run_id=run_id,
                is_postgres=is_postgres,
            )

        # =====================================================================
        # 3. Create/upsert workflow template node (from the workflow_nodes)
        # =====================================================================
        template_node_id = None
        if state.workflow_nodes and task_slug:
            template_key = f"template.{provider_code}.{task_slug}"
            # Build template properties from workflow nodes
            steps_data = [
                {
                    "key": n.node_key,
                    "type": n.node_type,
                    "label": n.config.get("label", n.node_key) if n.config else n.node_key,
                    "position": n.position,
                }
                for n in state.workflow_nodes
            ]
            template_props = {
                "steps": steps_data,
                "node_count": len(state.workflow_nodes),
                "edge_count": len(state.workflow_edges) if state.workflow_edges else 0,
            }

            # Create a compact description for embedding
            template_desc = f"Workflow template for {task_slug}: {task_description or ''}"
            template_embedding = _compute_embedding(template_desc)

            template_node_id = _upsert_kg_node(
                cur,
                node_type=KGNodeType.WORKFLOW_TEMPLATE.value,
                key=template_key,
                name=f"{task_slug} Template",
                provider_code=provider_code,
                description=template_desc,
                properties=template_props,
                embedding=template_embedding,
                run_id=run_id,
                is_postgres=is_postgres,
            )

            # Edge: task → template (task uses this template)
            if task_node_id:
                _upsert_kg_edge(
                    cur,
                    src_node_id=task_node_id,
                    dst_node_id=template_node_id,
                    relation_type=KGEdgeRelation.COMPOSED_OF.value,
                    run_id=run_id,
                    is_postgres=is_postgres,
                )

            # =====================================================================
            # 4. Create workflow steps in kg.workflow_steps
            # =====================================================================
            # Store step_key -> step_id mapping for step_bindings creation
            step_ids_by_key: Dict[str, int] = {}
            
            for wf_node in state.workflow_nodes:
                step_id = _upsert_workflow_step(
                    cur,
                    template_node_id=template_node_id,
                    step_key=wf_node.node_key,
                    step_type=wf_node.node_type,
                    position=wf_node.position,
                    label=wf_node.config.get("label") if wf_node.config else None,
                    description=wf_node.config.get("description") if wf_node.config else None,
                    config=wf_node.config,
                    is_postgres=is_postgres,
                )
                step_ids_by_key[wf_node.node_key] = step_id

            # =====================================================================
            # 4.1 KG-001 Fix: Create step_bindings linking steps to endpoints
            # This enables GraphRAG to recommend specific endpoints for workflow steps
            # =====================================================================
            step_bindings_created = 0
            if state.endpoint_bindings:
                # Build endpoint lookup by ID for efficient access
                endpoint_by_id = {ep.id: ep for ep in state.endpoints if ep.id}
                
                for binding in state.endpoint_bindings:
                    # Find the step this binding applies to
                    step_id = step_ids_by_key.get(binding.flow_node_key)
                    if not step_id:
                        logger.debug(f"No step found for binding flow_node_key={binding.flow_node_key}")
                        continue
                    
                    # Get endpoint details
                    endpoint = endpoint_by_id.get(binding.endpoint_id) if binding.endpoint_id else None
                    endpoint_path = endpoint.path if endpoint else None
                    endpoint_method = endpoint.method if endpoint else None
                    
                    # Look up the endpoint_node_id from kg.nodes
                    endpoint_node_id = None
                    if endpoint and provider_code:
                        endpoint_key = f"endpoint.{provider_code}.{endpoint_method}.{endpoint_path}"
                        if is_postgres:
                            cur.execute(
                                "SELECT id FROM kg.nodes WHERE key = %s AND node_type = %s",
                                (endpoint_key, KGNodeType.ENDPOINT.value)
                            )
                        else:
                            cur.execute(
                                "SELECT id FROM kg_nodes WHERE key = ? AND node_type = ?",
                                (endpoint_key, KGNodeType.ENDPOINT.value)
                            )
                        row = cur.fetchone()
                        endpoint_node_id = row[0] if row else None
                    
                    # Create the step_binding (even if endpoint_node_id is None for future linking)
                    if endpoint_path or endpoint_method or endpoint_node_id:
                        try:
                            _upsert_step_binding(
                                cur,
                                step_id=step_id,
                                endpoint_node_id=endpoint_node_id,
                                endpoint_path=endpoint_path,
                                endpoint_method=endpoint_method,
                                request_mapping=binding.request_mapping,
                                response_mapping=binding.response_mapping,
                                is_postgres=is_postgres,
                            )
                            step_bindings_created += 1
                        except Exception as e:
                            logger.warning(f"Failed to create step_binding for step={binding.flow_node_key}: {e}")
                
                if step_bindings_created > 0:
                    logger.debug(f"Created {step_bindings_created} step_bindings for template {template_key}")

            # =====================================================================
            # 4.5 Bug #62 Fix: Create pattern nodes and link templates to patterns
            # This enables cross-provider pattern matching via KG queries
            # =====================================================================
            # Only consider endpoints that are actually bound to this workflow
            bound_endpoint_ids = {b.endpoint_id for b in (state.endpoint_bindings or []) if b.endpoint_id}
            bound_endpoints = [ep for ep in state.endpoints if ep.id in bound_endpoint_ids]
            # Fallback: if no bindings, use all endpoints (shouldn't happen normally)
            if not bound_endpoints:
                bound_endpoints = state.endpoints[:5]  # Limit to avoid confusion
            
            pattern_key, pattern_confidence = _detect_pattern_from_workflow(
                state.workflow_nodes,
                bound_endpoints,
                task_description,
            )
            
            if pattern_key and pattern_key in STANDARD_PATTERNS:
                pattern = STANDARD_PATTERNS[pattern_key]
                pattern_node_key = f"pattern.{pattern_key}"
                
                # Create/upsert the pattern node (provider-agnostic, so provider_code=None)
                pattern_node_id = _upsert_kg_node(
                    cur,
                    node_type=KGNodeType.PATTERN.value,
                    key=pattern_node_key,
                    name=pattern["name"],
                    provider_code=None,  # Patterns are provider-agnostic
                    description=pattern["description"],
                    properties={
                        "pattern_key": pattern_key,
                        "http_methods": pattern.get("http_methods", []),
                        "steps": pattern.get("steps", []),
                        "path_pattern": pattern.get("path_pattern"),
                        "keywords": pattern.get("keywords", []),
                    },
                    run_id=run_id,
                    is_postgres=is_postgres,
                )
                
                # Edge: template → pattern (template implements this pattern)
                _upsert_kg_edge(
                    cur,
                    src_node_id=template_node_id,
                    dst_node_id=pattern_node_id,
                    relation_type=KGEdgeRelation.IMPLEMENTS_PATTERN.value,
                    properties={"confidence": pattern_confidence},
                    run_id=run_id,
                    is_postgres=is_postgres,
                )
                
                # Also update template properties to include pattern_key for quick access
                template_props["pattern_key"] = pattern_key
                template_props["pattern_confidence"] = pattern_confidence
                
                logger.debug(f"Linked template to pattern: {pattern_key} (confidence={pattern_confidence:.2f})")

        # =====================================================================
        # 5. Create entity nodes and edges
        # =====================================================================
        entity_node_ids = {}
        for entity in state.entities:
            entity_key = f"entity.{provider_code}.{entity.name}"
            entity_node_id = _upsert_kg_node(
                cur,
                node_type=KGNodeType.ENTITY.value,
                key=entity_key,
                name=entity.name,
                provider_code=provider_code,
                description=entity.description,
                run_id=run_id,
                is_postgres=is_postgres,
            )
            entity_node_ids[entity.name] = entity_node_id

            # Edge: entity → provider
            _upsert_kg_edge(
                cur,
                src_node_id=entity_node_id,
                dst_node_id=provider_node_id,
                relation_type=KGEdgeRelation.BELONGS_TO_PROVIDER.value,
                run_id=run_id,
                is_postgres=is_postgres,
            )

        # Create edges for task → entities (if we know input/output entities)
        if task_node_id and state.integration_task:
            # Output entities
            output_entities = state.integration_task.output_entities or []
            for entity_name in output_entities:
                if entity_name in entity_node_ids:
                    _upsert_kg_edge(
                        cur,
                        src_node_id=task_node_id,
                        dst_node_id=entity_node_ids[entity_name],
                        relation_type=KGEdgeRelation.PRODUCES_ENTITY.value,
                        run_id=run_id,
                        is_postgres=is_postgres,
                    )

            # Input entities
            input_entities = state.integration_task.input_entities or []
            for entity_name in input_entities:
                if entity_name in entity_node_ids:
                    _upsert_kg_edge(
                        cur,
                        src_node_id=task_node_id,
                        dst_node_id=entity_node_ids[entity_name],
                        relation_type=KGEdgeRelation.CONSUMES_ENTITY.value,
                        run_id=run_id,
                        is_postgres=is_postgres,
                    )

        # =====================================================================
        # 6. Create endpoint nodes and edges
        # =====================================================================
        endpoint_node_ids = {}
        for endpoint in state.endpoints[:50]:  # Limit to avoid huge KG for large specs
            endpoint_key = f"endpoint.{provider_code}.{endpoint.method}.{endpoint.path}"
            endpoint_node_id = _upsert_kg_node(
                cur,
                node_type=KGNodeType.ENDPOINT.value,
                key=endpoint_key,
                name=f"{endpoint.method} {endpoint.path}",
                provider_code=provider_code,
                description=endpoint.summary or endpoint.description,
                properties={
                    "method": endpoint.method,
                    "path": endpoint.path,
                    "operation_id": endpoint.operation_id,
                },
                run_id=run_id,
                is_postgres=is_postgres,
            )
            endpoint_node_ids[f"{endpoint.method}:{endpoint.path}"] = endpoint_node_id

            # Edge: endpoint → provider
            _upsert_kg_edge(
                cur,
                src_node_id=endpoint_node_id,
                dst_node_id=provider_node_id,
                relation_type=KGEdgeRelation.BELONGS_TO_PROVIDER.value,
                run_id=run_id,
                is_postgres=is_postgres,
            )

        # Create edges: template → endpoints (for api_call steps)
        if template_node_id and state.endpoint_bindings:
            for binding in state.endpoint_bindings:
                # Find the endpoint this binding references
                if binding.endpoint_id:
                    # Look up endpoint by ID
                    for ep in state.endpoints:
                        if ep.id == binding.endpoint_id:
                            ep_key = f"{ep.method}:{ep.path}"
                            if ep_key in endpoint_node_ids:
                                _upsert_kg_edge(
                                    cur,
                                    src_node_id=template_node_id,
                                    dst_node_id=endpoint_node_ids[ep_key],
                                    relation_type=KGEdgeRelation.USES_ENDPOINT.value,
                                    properties={"flow_node_key": binding.flow_node_key},
                                    run_id=run_id,
                                    is_postgres=is_postgres,
                                )
                            break

        # =====================================================================
        # 7. V1.1 FT-011: Track usage of matched templates and patterns
        # Increment usage_count for any templates/patterns that were matched
        # during align_task_with_kg. This enables learning from successful runs.
        # =====================================================================
        if state.plan:
            # Track usage of matched template
            matched_template_id = state.plan.get("matched_template_id")
            if matched_template_id:
                if _increment_usage_count(cur, matched_template_id, is_postgres):
                    logger.debug(f"Incremented usage_count for template: {matched_template_id}")
                    state.persisted_ids["kg_template_usage_tracked"] = matched_template_id
            
            # Track usage of matched pattern
            matched_pattern_id = state.plan.get("matched_pattern_id")
            if matched_pattern_id:
                if _increment_usage_count(cur, matched_pattern_id, is_postgres):
                    logger.debug(f"Incremented usage_count for pattern: {matched_pattern_id}")
                    state.persisted_ids["kg_pattern_usage_tracked"] = matched_pattern_id

        conn.commit()

        # Update persisted_ids with KG info
        state.persisted_ids.update({
            "kg_learning": "completed",
            "kg_nodes_created": len(entity_node_ids) + len(endpoint_node_ids) + (3 if task_node_id else 1),
            "kg_template_node_id": template_node_id,
            "kg_task_node_id": task_node_id,
        })

        logger.info(f"KG learning complete: {len(entity_node_ids)} entities, {len(endpoint_node_ids)} endpoints, template={template_node_id}")

        # =====================================================================
        # 8. PL-001: Capture run events for pattern discovery
        # =====================================================================
        try:
            from integration_coworker.kg.pattern_discovery import (
                capture_run_events,
                check_and_promote_candidates,
            )
            
            events_captured = capture_run_events(
                run_id=run_id,
                workflow_nodes=state.workflow_nodes,
                provider_code=provider_code,
                endpoints=state.endpoints,
            )
            
            if events_captured > 0:
                state.persisted_ids["kg_events_captured"] = events_captured
                
                # Check if any candidates should be promoted
                promoted = check_and_promote_candidates()
                if promoted:
                    state.persisted_ids["kg_patterns_promoted"] = promoted
                    logger.info(f"Auto-promoted {len(promoted)} pattern(s): {promoted}")
                    
        except Exception as e:
            # Pattern learning failure is non-critical
            logger.warning(f"Pattern event capture failed (non-critical): {e}")

        state.completed_steps.append("persist_kg_learning")

    except Exception as e:
        logger.error(f"KG learning failed: {e}")
        state.errors.append(f"KG learning failed: {str(e)}")
        state.persisted_ids.update({"kg_learning": "failed", "kg_error": str(e)})
        state.completed_steps.append("persist_kg_learning")

    return state
