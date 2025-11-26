"""
Persist Gold Checkpoint Node

Per design doc Section 5.4 and Appendix C.3:
This checkpoint persists all Gold-layer data after generate_code_and_tests.

Writes to:
- integration_tasks
- workflow_templates
- integration_flow_nodes
- integration_flow_edges
- endpoint_bindings
- policies
- code_artifacts

Requires Silver checkpoint to have run first (needs source_system_id, endpoint IDs, etc.)
"""
from datetime import datetime, UTC
import json
import logging
from typing import Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence import db

logger = logging.getLogger(__name__)


def persist_gold_checkpoint(state: WorkflowState) -> WorkflowState:
    """
    Persist Gold layer data to database.
    
    Per design doc Appendix C.3:
    - Runs after generate_code_and_tests
    - Depends on Silver checkpoint for IDs
    - Only persist_* nodes may write to the database
    
    Reads: integration_task, workflow_nodes, workflow_edges, endpoint_bindings,
           policies, code_artifacts, persisted_ids (from Silver checkpoint)
    Writes: persisted_ids (gold subset), backfills IDs in state objects
    """
    is_dry_run = state.options.dry_run if state.options else False
    
    if is_dry_run:
        state.persisted_ids.update({
            "gold_dry_run": True,
            "would_persist_gold": {
                "integration_task": 1 if state.integration_task else 0,
                "workflow_nodes": len(state.workflow_nodes),
                "workflow_edges": len(state.workflow_edges),
                "endpoint_bindings": len(state.endpoint_bindings),
                "policies": len(state.policies),
                "code_artifacts": len(state.code_artifacts),
            },
        })
        state.completed_steps.append("persist_gold_checkpoint")
        return state
    
    try:
        # Get Silver IDs from state
        source_system_id = state.persisted_ids.get("source_system_id")
        spec_document_id = state.persisted_ids.get("spec_document_id")
        
        if not source_system_id:
            raise ValueError("Silver checkpoint must run before Gold checkpoint (no source_system_id)")
        
        db.init_schema()
        conn = db.get_connection()  # Uses Postgres or SQLite based on config
        cur = conn.cursor()
        
        provider_code = state.provider_code or "unknown"
        
        # 1. Insert IntegrationTask
        task_id = None
        if state.integration_task:
            task = state.integration_task
            task_slug = task.task_slug if hasattr(task, 'task_slug') and task.task_slug else \
                        task.description.lower().replace(" ", "_")[:50]
            
            cur.execute(
                """INSERT OR IGNORE INTO integration_tasks 
                   (provider_code, task_slug, description, source_system_id, target_spec_document_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (provider_code, task_slug, task.description, source_system_id, spec_document_id)
            )
            cur.execute(
                "SELECT id FROM integration_tasks WHERE provider_code = ? AND task_slug = ?",
                (provider_code, task_slug)
            )
            task_id = cur.fetchone()[0]
            state.integration_task.id = task_id
            state.integration_task.task_slug = task_slug
            state.integration_task.source_system_id = source_system_id
        
        # 2. Insert WorkflowTemplate (if exists)
        if state.workflow_template:
            cur.execute(
                """INSERT OR IGNORE INTO workflow_templates 
                   (source_system_id, code, name, description)
                   VALUES (?, ?, ?, ?)""",
                (source_system_id, state.workflow_template.code, 
                 state.workflow_template.name, state.workflow_template.description)
            )
            cur.execute(
                "SELECT id FROM workflow_templates WHERE source_system_id = ? AND code = ?",
                (source_system_id, state.workflow_template.code)
            )
            template_id = cur.fetchone()[0]
            state.workflow_template.id = template_id
        
        # 3. Insert FlowNodes
        for idx, node in enumerate(state.workflow_nodes):
            config_json = json.dumps({"label": node.label, **(node.config or {})}) if node.label or node.config else "{}"
            cur.execute(
                """INSERT OR IGNORE INTO integration_flow_nodes 
                   (task_id, node_key, node_type, position, config)
                   VALUES (?, ?, ?, ?, ?)""",
                (task_id, node.node_key, node.node_type, idx, config_json)
            )
            cur.execute(
                "SELECT id FROM integration_flow_nodes WHERE task_id = ? AND node_key = ?",
                (task_id, node.node_key)
            )
            node_id = cur.fetchone()[0]
            node.id = node_id
            node.task_id = task_id
        
        # 4. Insert FlowEdges
        for edge in state.workflow_edges:
            condition_str = json.dumps(edge.condition) if edge.condition else None
            cur.execute(
                """INSERT OR IGNORE INTO integration_flow_edges 
                   (task_id, from_node_key, to_node_key, condition)
                   VALUES (?, ?, ?, ?)""",
                (task_id, edge.from_node_key, edge.to_node_key, condition_str)
            )
            cur.execute(
                "SELECT id FROM integration_flow_edges WHERE task_id = ? AND from_node_key = ? AND to_node_key = ?",
                (task_id, edge.from_node_key, edge.to_node_key)
            )
            edge_id = cur.fetchone()[0]
            edge.id = edge_id
            edge.task_id = task_id
        
        # 5. Insert EndpointBindings
        # Build endpoint lookup by operation_id / method+path
        endpoint_ids_by_key = {}
        for endpoint in state.endpoints:
            if endpoint.id:
                key = (endpoint.method, endpoint.path, endpoint.operation_id)
                endpoint_ids_by_key[key] = endpoint.id
                # Also index by operation_id alone
                if endpoint.operation_id:
                    endpoint_ids_by_key[endpoint.operation_id] = endpoint.id
        
        for binding in state.endpoint_bindings:
            endpoint_id = binding.endpoint_id
            
            # Try to resolve endpoint_id if not set
            if not endpoint_id and state.integration_task and state.integration_task.constraints:
                target_ops = state.integration_task.constraints.get("extra", {}).get("target_operations", [])
                if target_ops:
                    op = target_ops[0]
                    operation_id = op.get("operation_id")
                    if operation_id and operation_id in endpoint_ids_by_key:
                        endpoint_id = endpoint_ids_by_key[operation_id]
                    else:
                        method = op.get("method")
                        path = op.get("path")
                        for key, ep_id in endpoint_ids_by_key.items():
                            if isinstance(key, tuple) and len(key) == 3:
                                if key[0] == method and key[1] == path:
                                    endpoint_id = ep_id
                                    break
            
            # Fall back to first endpoint if still not resolved
            if not endpoint_id and endpoint_ids_by_key:
                endpoint_id = list(endpoint_ids_by_key.values())[0]
            
            if endpoint_id:
                request_json = json.dumps(binding.request_mapping or {})
                response_json = json.dumps(binding.response_mapping or {})
                
                cur.execute(
                    """INSERT OR IGNORE INTO endpoint_bindings 
                       (task_id, flow_node_key, endpoint_id, request_mapping, response_mapping)
                       VALUES (?, ?, ?, ?, ?)""",
                    (task_id, binding.flow_node_key, endpoint_id, request_json, response_json)
                )
                cur.execute(
                    "SELECT id, endpoint_id FROM endpoint_bindings WHERE task_id = ? AND flow_node_key = ? AND endpoint_id = ?",
                    (task_id, binding.flow_node_key, endpoint_id)
                )
                row = cur.fetchone()
                if row:
                    binding.id = row[0]
                    binding.endpoint_id = row[1]
                    binding.task_id = task_id
        
        # 6. Insert Policies
        for policy in state.policies:
            config_json = json.dumps(policy.config or {})
            cur.execute(
                """INSERT OR IGNORE INTO policies 
                   (task_id, policy_type, scope, scope_ref, config)
                   VALUES (?, ?, ?, ?, ?)""",
                (task_id, policy.policy_type, policy.scope, policy.scope_ref, config_json)
            )
            cur.execute(
                "SELECT id FROM policies WHERE task_id = ? AND policy_type = ? AND scope = ?",
                (task_id, policy.policy_type, policy.scope)
            )
            row = cur.fetchone()
            if row:
                policy.id = row[0]
                policy.task_id = task_id
        
        # 7. Insert CodeArtifacts
        for artifact in state.code_artifacts:
            cur.execute(
                """INSERT OR IGNORE INTO code_artifacts 
                   (task_id, artifact_type, rel_path, language, content)
                   VALUES (?, ?, ?, ?, ?)""",
                (task_id, artifact.artifact_type, artifact.rel_path, artifact.language, artifact.content)
            )
            cur.execute(
                "SELECT id FROM code_artifacts WHERE task_id = ? AND rel_path = ? AND artifact_type = ?",
                (task_id, artifact.rel_path, artifact.artifact_type)
            )
            artifact_id = cur.fetchone()[0]
            artifact.id = artifact_id
            artifact.task_id = task_id
        
        conn.commit()
        conn.close()
        
        # Update persisted_ids
        state.persisted_ids.update({
            "gold_checkpoint": "completed",
            "task_id": task_id,
            "integration_task_id": task_id,
            "workflow_node_count": len(state.workflow_nodes),
            "workflow_edge_count": len(state.workflow_edges),
            "endpoint_binding_count": len(state.endpoint_bindings),
            "policy_count": len(state.policies),
            "code_artifact_count": len(state.code_artifacts),
            "gold_timestamp": datetime.now(UTC).isoformat(),
        })
        
        state.completed_steps.append("persist_gold_checkpoint")
        logger.info(f"Gold checkpoint persisted: task_id={task_id}, {len(state.code_artifacts)} artifacts")
        return state
        
    except Exception as e:
        state.errors.append(f"Gold checkpoint failed: {str(e)}")
        state.persisted_ids.update({
            "gold_checkpoint": "failed",
            "gold_error": str(e),
        })
        logger.error(f"Gold checkpoint failed: {e}")
        return state
