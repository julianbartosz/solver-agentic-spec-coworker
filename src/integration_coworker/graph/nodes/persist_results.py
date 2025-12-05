"""
Persist Results Node

M4 P2.2: Single node responsible for all database writes.
Upserts Silver + Gold records and backfills IDs into state objects.

Supports both Postgres (primary) and SQLite (fallback) using sql_helpers.

Note: This is the legacy persist node, kept for backward compatibility.
The new design uses checkpoint nodes: persist_silver_checkpoint, 
persist_gold_checkpoint, and persist_run_outcome.
"""
from datetime import datetime, timezone
import json

from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence import db
from integration_coworker.persistence.sql_helpers import (
    upsert_ignore, select_by_columns, get_engine_type
)

# For Python 3.10 compatibility (UTC was added in 3.11)
UTC = timezone.utc

# Schema prefixes for Postgres tables
SILVER_SCHEMA = "spec_silver"
GOLD_SCHEMA = "integration_gold"


def persist_results(state: WorkflowState) -> WorkflowState:
    """
    Persist all Silver and Gold data to database.
    
    Reads: All Silver drafts, All Gold drafts, Embeddings, run_id, errors, options.dry_run
    Writes: persisted_ids, run status summary, backfills IDs in state objects
    """
    is_dry_run = state.options.dry_run if state.options else False

    if is_dry_run:
        # Don't write to DB, just log what would be persisted
        summary = {
            "dry_run": True,
            "would_persist": {
                "source_system": 1 if state.source_system else 0,
                "spec_documents": len(state.spec_documents),
                "endpoints": len(state.endpoints),
                "schemas": len(state.schemas),
                "entities": len(state.entities),
                "integration_task": 1 if state.integration_task else 0,
                "workflow_nodes": len(state.workflow_nodes),
                "policies": len(state.policies),
                "code_artifacts": len(state.code_artifacts),
            },
            "run_status": "completed_dry_run",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        state.persisted_ids = summary
        state.completed_steps.append("persist_results")
        return state

    # Real database persistence (Postgres or SQLite)
    try:
        # Initialize schema if needed
        db.init_schema()
        conn = db.get_connection()
        cur = conn.cursor()

        # Determine engine type for schema prefixes
        engine = get_engine_type()
        silver_schema = SILVER_SCHEMA if engine == "postgres" else None
        gold_schema = GOLD_SCHEMA if engine == "postgres" else None

        # 1. Upsert SourceSystem
        provider_code = state.provider_code or "unknown"
        sql = upsert_ignore("source_systems", ["code", "display_name"], ["code"], silver_schema)
        cur.execute(sql, (provider_code, provider_code.replace("_", " ").title()))

        sql = select_by_columns("source_systems", ["id"], ["code"], silver_schema)
        cur.execute(sql, (provider_code,))
        source_system_id = cur.fetchone()[0]

        # Backfill into state.source_system if exists
        if state.source_system:
            state.source_system.id = source_system_id

        # 2. Insert SpecDocument
        spec_document_id = None
        if state.spec_documents:
            spec_doc = state.spec_documents[0]
            # Per design doc Appendix B.2: spec_documents uses 'uri' column
            sql = upsert_ignore(
                "spec_documents",
                ["source_system_id", "uri", "sha256", "content_type"],
                ["source_system_id", "sha256"],
                silver_schema
            )
            cur.execute(sql, (source_system_id, spec_doc.uri, spec_doc.sha256, spec_doc.content_type))

            sql = select_by_columns("spec_documents", ["id"], ["source_system_id", "sha256"], silver_schema)
            cur.execute(sql, (source_system_id, spec_doc.sha256))
            spec_document_id = cur.fetchone()[0]
            state.spec_documents[0].id = spec_document_id

        # 3. Insert Schemas (needed for endpoint FK)
        # Per design doc Appendix B.2: schemas uses source_system_id not spec_document_id
        schema_ids_by_name = {}
        for schema_obj in state.schemas:
            sql = upsert_ignore("schemas", ["source_system_id", "name", "ref"], ["source_system_id", "name"], silver_schema)
            cur.execute(sql, (source_system_id, schema_obj.name, schema_obj.ref))

            sql = select_by_columns("schemas", ["id"], ["source_system_id", "name"], silver_schema)
            cur.execute(sql, (source_system_id, schema_obj.name))
            schema_id = cur.fetchone()[0]
            schema_obj.id = schema_id
            schema_ids_by_name[schema_obj.name] = schema_id

        # 4. Insert Endpoints
        # Per design doc Appendix B.2: endpoints has source_system_id + spec_document_id
        endpoint_ids_by_key = {}
        for endpoint in state.endpoints:
            sql = upsert_ignore(
                "endpoints",
                ["source_system_id", "spec_document_id", "method", "path", "operation_id", "summary"],
                ["source_system_id", "spec_document_id", "path", "method"],
                silver_schema
            )
            cur.execute(sql, (source_system_id, spec_document_id, endpoint.method, endpoint.path, endpoint.operation_id, endpoint.summary))

            sql = select_by_columns("endpoints", ["id"], ["source_system_id", "spec_document_id", "method", "path"], silver_schema)
            cur.execute(sql, (source_system_id, spec_document_id, endpoint.method, endpoint.path))
            endpoint_id = cur.fetchone()[0]
            endpoint.id = endpoint_id
            # Create lookup key for bindings
            key = (endpoint.method, endpoint.path, endpoint.operation_id)
            endpoint_ids_by_key[key] = endpoint_id

        # 5. Insert Entities
        for entity in state.entities:
            sql = upsert_ignore("entities", ["source_system_id", "name", "description"], ["source_system_id", "name"], silver_schema)
            cur.execute(sql, (source_system_id, entity.name, entity.description))

            sql = select_by_columns("entities", ["id"], ["source_system_id", "name"], silver_schema)
            cur.execute(sql, (source_system_id, entity.name))
            entity_id = cur.fetchone()[0]
            entity.id = entity_id

        # 6. Insert IntegrationTask
        # Per design doc Appendix B.3: uses provider_code + task_slug as unique key
        task_id = None
        if state.integration_task:
            task = state.integration_task
            # Use existing task_slug or generate from description if not set
            task_slug = task.task_slug if hasattr(task, 'task_slug') and task.task_slug else task.description.lower().replace(" ", "_")[:50]

            sql = upsert_ignore(
                "integration_tasks",
                ["provider_code", "task_slug", "description", "source_system_id", "target_spec_document_id"],
                ["provider_code", "task_slug"],
                gold_schema
            )
            cur.execute(sql, (provider_code, task_slug, task.description, source_system_id, spec_document_id))

            sql = select_by_columns("integration_tasks", ["id"], ["provider_code", "task_slug"], gold_schema)
            cur.execute(sql, (provider_code, task_slug))
            task_id = cur.fetchone()[0]
            state.integration_task.id = task_id
            # Backfill task_slug if it wasn't set
            if not hasattr(task, 'task_slug') or not task.task_slug:
                state.integration_task.task_slug = task_slug

        # 7. Insert FlowNodes
        # Per design doc Appendix B.3: uses config (JSON) instead of label
        for idx, node in enumerate(state.workflow_nodes):
            config_json = json.dumps({"label": node.label}) if node.label else "{}"
            sql = upsert_ignore(
                "integration_flow_nodes",
                ["task_id", "node_key", "node_type", "position", "config"],
                ["task_id", "node_key"],
                gold_schema
            )
            cur.execute(sql, (task_id, node.node_key, node.node_type, idx, config_json))

            sql = select_by_columns("integration_flow_nodes", ["id"], ["task_id", "node_key"], gold_schema)
            cur.execute(sql, (task_id, node.node_key))
            node_id = cur.fetchone()[0]
            node.id = node_id

        # 8. Insert FlowEdges
        for edge in state.workflow_edges:
            # Serialize condition to TEXT (not JSON column in schema)
            condition_str = json.dumps(edge.condition) if edge.condition else None
            sql = upsert_ignore(
                "integration_flow_edges",
                ["task_id", "from_node_key", "to_node_key", "condition"],
                ["task_id", "from_node_key", "to_node_key"],
                gold_schema
            )
            cur.execute(sql, (task_id, edge.from_node_key, edge.to_node_key, condition_str))

            sql = select_by_columns("integration_flow_edges", ["id"], ["task_id", "from_node_key", "to_node_key"], gold_schema)
            cur.execute(sql, (task_id, edge.from_node_key, edge.to_node_key))
            edge_id = cur.fetchone()[0]
            edge.id = edge_id
            edge.task_id = task_id

        # 9. Insert EndpointBindings and backfill endpoint_id
        # Per design doc Appendix B.3: uses request_mapping/response_mapping (not _json suffix)
        for binding in state.endpoint_bindings:
            # Match endpoint by inferring from task constraints (target operation)
            endpoint_id = binding.endpoint_id

            # If not set, infer from the first target operation in task constraints
            if not endpoint_id and state.integration_task and state.integration_task.constraints:
                target_ops = state.integration_task.constraints.get("extra", {}).get("target_operations", [])
                if target_ops and len(target_ops) > 0:
                    # Use first target operation to find matching endpoint
                    op = target_ops[0]
                    operation_id = op.get("operation_id")
                    method = op.get("method")
                    path = op.get("path")

                    # Try to find by operation_id first, then by method+path
                    for key, ep_id in endpoint_ids_by_key.items():
                        ep_method, ep_path, ep_op_id = key
                        if operation_id and ep_op_id == operation_id:
                            endpoint_id = ep_id
                            break
                        elif method and path and ep_method == method and ep_path == path:
                            endpoint_id = ep_id
                            break

            # Serialize mappings to JSON
            request_json = json.dumps(binding.request_mapping or {})
            response_json = json.dumps(binding.response_mapping or {})

            # Use endpoint_id or default to first endpoint if available
            if not endpoint_id and endpoint_ids_by_key:
                endpoint_id = list(endpoint_ids_by_key.values())[0]

            if endpoint_id:
                sql = upsert_ignore(
                    "endpoint_bindings",
                    ["task_id", "flow_node_key", "endpoint_id", "request_mapping", "response_mapping"],
                    ["task_id", "flow_node_key", "endpoint_id"],
                    gold_schema
                )
                cur.execute(sql, (task_id, binding.flow_node_key, endpoint_id, request_json, response_json))

                sql = select_by_columns("endpoint_bindings", ["id", "endpoint_id"], ["task_id", "flow_node_key", "endpoint_id"], gold_schema)
                cur.execute(sql, (task_id, binding.flow_node_key, endpoint_id))
                row = cur.fetchone()
                if row:
                    binding.id = row[0]
                    binding.endpoint_id = row[1]  # Backfill if was inferred

        # 10. Insert CodeArtifacts
        # Per design doc Appendix B.3: unique key is (task_id, rel_path, artifact_type)
        for artifact in state.code_artifacts:
            sql = upsert_ignore(
                "code_artifacts",
                ["task_id", "artifact_type", "rel_path", "language", "content"],
                ["task_id", "rel_path", "artifact_type"],
                gold_schema
            )
            cur.execute(sql, (task_id, artifact.artifact_type, artifact.rel_path, artifact.language, artifact.content))

            sql = select_by_columns("code_artifacts", ["id"], ["task_id", "rel_path", "artifact_type"], gold_schema)
            cur.execute(sql, (task_id, artifact.rel_path, artifact.artifact_type))
            artifact_id = cur.fetchone()[0]
            artifact.id = artifact_id
            artifact.task_id = task_id

        conn.commit()

        # Build summary
        state.persisted_ids = {
            "run_status": "completed",
            "source_system_id": source_system_id,
            "spec_document_id": spec_document_id,
            "task_id": task_id,  # For backward compat with tests
            "integration_task_id": task_id,
            "endpoint_count": len(state.endpoints),
            "schema_count": len(state.schemas),
            "entity_count": len(state.entities),
            "workflow_node_count": len(state.workflow_nodes),
            "policy_count": len(state.policies),
            "code_artifact_count": len(state.code_artifacts),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        conn.close()
        state.completed_steps.append("persist_results")
        return state

    except Exception as e:
        state.errors.append(f"Persistence failed: {str(e)}")
        state.persisted_ids = {
            "run_status": "failed",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return state
