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
"""
import json
import logging
from datetime import datetime, UTC
from typing import Optional, List, Dict, Any

from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence import db
from integration_coworker.config import get_settings
from integration_coworker.domain.models import KGNodeType, KGEdgeRelation

logger = logging.getLogger(__name__)


def _compute_embedding(text: str) -> Optional[List[float]]:
    """Compute embedding for text using OpenAI API (if available)."""
    settings = get_settings()
    if not settings.llm.api_key or settings.llm.use_mock:
        logger.debug("Embedding computation skipped: mock LLM or no API key")
        return None
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.llm.api_key)
        response = client.embeddings.create(
            model=settings.llm.embedding_model or "text-embedding-3-small",
            input=text[:8000],  # Truncate to avoid token limits
        )
        return response.data[0].embedding
    except Exception as e:
        logger.warning(f"Failed to compute embedding: {e}")
        return None


# Track whether we've logged embedding degradation for this run
_logged_embedding_warning = False


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
            for wf_node in state.workflow_nodes:
                _upsert_workflow_step(
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
        
        conn.commit()
        
        # Update persisted_ids with KG info
        state.persisted_ids.update({
            "kg_learning": "completed",
            "kg_nodes_created": len(entity_node_ids) + len(endpoint_node_ids) + (3 if task_node_id else 1),
            "kg_template_node_id": template_node_id,
            "kg_task_node_id": task_node_id,
        })
        
        logger.info(f"KG learning complete: {len(entity_node_ids)} entities, {len(endpoint_node_ids)} endpoints, template={template_node_id}")
        
        state.completed_steps.append("persist_kg_learning")
        
    except Exception as e:
        logger.error(f"KG learning failed: {e}")
        state.errors.append(f"KG learning failed: {str(e)}")
        state.persisted_ids.update({"kg_learning": "failed", "kg_error": str(e)})
        state.completed_steps.append("persist_kg_learning")
    
    return state
