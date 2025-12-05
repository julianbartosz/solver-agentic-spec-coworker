from datetime import datetime, timezone
import json
from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence import db

def persist_results(state: WorkflowState) -> WorkflowState:
    """
    Reads: All Silver drafts, All Gold drafts, Embeddings, run_id, errors, options.dry_run
    Writes: persisted_ids, run status summary, backfills IDs in state objects
    """
    is_dry_run = state.options.dry_run if state.options else False

    if is_dry_run:
        # Don't write to DB, just log what would be persisted
        summary = {
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
    else:
        # M4: Real SQLite persistence
        try:
            # Initialize schema if needed
            db.init_schema()
            conn = db.get_connection()
            cur = conn.cursor()

            # 1. Upsert SourceSystem
            provider_code = state.provider_code or "unknown"
            cur.execute(
                "INSERT OR IGNORE INTO source_systems (code, display_name) VALUES (?, ?)",
                (provider_code, provider_code.replace("_", " ").title())
            )
            cur.execute("SELECT id FROM source_systems WHERE code = ?", (provider_code,))
            source_system_id = cur.fetchone()[0]

            # 2. Insert SpecDocument
            spec_document_id = None
            if state.spec_documents:
                spec_doc = state.spec_documents[0]
                cur.execute(
                    "INSERT OR IGNORE INTO spec_documents (source_system_id, path, sha256, content_type) VALUES (?, ?, ?, ?)",
                    (source_system_id, spec_doc.uri, spec_doc.sha256, spec_doc.content_type)
                )
                cur.execute(
                    "SELECT id FROM spec_documents WHERE source_system_id = ? AND sha256 = ?",
                    (source_system_id, spec_doc.sha256)
                )
                spec_document_id = cur.fetchone()[0]
                state.spec_documents[0].id = spec_document_id

            # 3. Insert Schemas (needed for endpoint FK)
            schema_ids_by_name = {}
            for schema in state.schemas:
                cur.execute(
                    "INSERT OR IGNORE INTO schemas (spec_document_id, name, ref) VALUES (?, ?, ?)",
                    (spec_document_id, schema.name, schema.ref)
                )
                cur.execute(
                    "SELECT id FROM schemas WHERE spec_document_id = ? AND name = ?",
                    (spec_document_id, schema.name)
                )
                schema_id = cur.fetchone()[0]
                schema.id = schema_id
                schema_ids_by_name[schema.name] = schema_id

            # 4. Insert Endpoints
            endpoint_ids_by_key = {}
            for endpoint in state.endpoints:
                cur.execute(
                    "INSERT OR IGNORE INTO endpoints (spec_document_id, method, path, operation_id, summary) VALUES (?, ?, ?, ?, ?)",
                    (spec_document_id, endpoint.method, endpoint.path, endpoint.operation_id, endpoint.summary)
                )
                cur.execute(
                    "SELECT id FROM endpoints WHERE spec_document_id = ? AND method = ? AND path = ?",
                    (spec_document_id, endpoint.method, endpoint.path)
                )
                endpoint_id = cur.fetchone()[0]
                endpoint.id = endpoint_id
                # Create lookup key for bindings
                key = (endpoint.method, endpoint.path, endpoint.operation_id)
                endpoint_ids_by_key[key] = endpoint_id

            # P1.3: Link request/response schema IDs for endpoints
            for endpoint in state.endpoints:
                # Check for temporary schema name attributes set by build_silver_api_model
                req_schema_id = None
                resp_schema_id = None

                if hasattr(endpoint, '_request_schema_name') and endpoint._request_schema_name:
                    req_schema_id = schema_ids_by_name.get(endpoint._request_schema_name)

                if hasattr(endpoint, '_response_schema_name') and endpoint._response_schema_name:
                    resp_schema_id = schema_ids_by_name.get(endpoint._response_schema_name)

                # Update endpoint record if we have schema IDs to link
                if req_schema_id is not None or resp_schema_id is not None:
                    cur.execute(
                        "UPDATE endpoints SET request_schema_id = ?, response_schema_id = ? WHERE id = ?",
                        (req_schema_id, resp_schema_id, endpoint.id)
                    )
                    # Backfill into state
                    endpoint.request_schema_id = req_schema_id
                    endpoint.response_schema_id = resp_schema_id

            # 5. Insert Entities
            for entity in state.entities:
                schema_id = schema_ids_by_name.get(entity.name)  # Link by name if exists
                cur.execute(
                    "INSERT OR IGNORE INTO entities (source_system_id, name, schema_id, description) VALUES (?, ?, ?, ?)",
                    (source_system_id, entity.name, schema_id, entity.description)
                )
                cur.execute(
                    "SELECT id FROM entities WHERE source_system_id = ? AND name = ?",
                    (source_system_id, entity.name)
                )
                entity.id = cur.fetchone()[0]

            # 6. Insert IntegrationTask
            task_id = None
            if state.integration_task:
                task = state.integration_task
                cur.execute(
                    "INSERT OR IGNORE INTO integration_tasks (source_system_id, task_slug, provider_code, description, target_spec_document_id) VALUES (?, ?, ?, ?, ?)",
                    (source_system_id, task.task_slug, task.provider_code, task.description, spec_document_id)
                )
                cur.execute(
                    "SELECT id FROM integration_tasks WHERE source_system_id = ? AND task_slug = ?",
                    (source_system_id, task.task_slug)
                )
                task_id = cur.fetchone()[0]
                task.id = task_id
                task.source_system_id = source_system_id
                task.target_spec_document_id = spec_document_id

            # 7. Insert FlowNodes
            for node in state.workflow_nodes:
                cur.execute(
                    "INSERT OR IGNORE INTO integration_flow_nodes (task_id, node_key, node_type, label, position) VALUES (?, ?, ?, ?, ?)",
                    (task_id, node.node_key, node.node_type, node.config.get("label") if node.config else node.node_key, node.position)
                )
                cur.execute(
                    "SELECT id FROM integration_flow_nodes WHERE task_id = ? AND node_key = ?",
                    (task_id, node.node_key)
                )
                node.id = cur.fetchone()[0]
                node.task_id = task_id

            # 8. Insert FlowEdges
            for edge in state.workflow_edges:
                cur.execute(
                    "INSERT OR IGNORE INTO integration_flow_edges (task_id, from_node_key, to_node_key, condition) VALUES (?, ?, ?, ?)",
                    (task_id, edge.from_node_key, edge.to_node_key, edge.condition)
                )
                cur.execute("SELECT last_insert_rowid()")
                edge.id = cur.fetchone()[0]
                edge.task_id = task_id

            # 9. Insert EndpointBindings and backfill endpoint_id
            for binding in state.endpoint_bindings:
                # Try to match endpoint by operation_id or method/path
                endpoint_id = None
                if state.integration_task and state.integration_task.constraints:
                    target_ops = state.integration_task.constraints.get("extra", {}).get("target_operations", [])
                    if target_ops:
                        op = target_ops[0]
                        key = (op.get("method"), op.get("path"), op.get("operation_id"))
                        endpoint_id = endpoint_ids_by_key.get(key)

                # Serialize mappings to JSON
                request_json = json.dumps(binding.request_mapping or {})
                response_json = json.dumps(binding.response_mapping or {})

                cur.execute(
                    "INSERT OR IGNORE INTO endpoint_bindings (task_id, flow_node_key, endpoint_id, request_mapping_json, response_mapping_json) VALUES (?, ?, ?, ?, ?)",
                    (task_id, binding.flow_node_key, endpoint_id, request_json, response_json)
                )
                cur.execute(
                    "SELECT id, endpoint_id FROM endpoint_bindings WHERE task_id = ? AND flow_node_key = ?",
                    (task_id, binding.flow_node_key)
                )
                row = cur.fetchone()
                binding.id = row[0]
                binding.task_id = task_id
                binding.endpoint_id = row[1]  # Backfill from DB

            conn.commit()
            conn.close()

            # Store summary
            state.persisted_ids = {
                "source_system_id": source_system_id,
                "spec_document_id": spec_document_id,
                "integration_task_id": task_id,
                "endpoint_count": len(state.endpoints),
                "schema_count": len(state.schemas),
                "run_status": "completed" if not state.errors else "completed_with_errors",
                "error_count": len(state.errors),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            state.errors.append(f"Persistence failed: {str(e)}")
            state.persisted_ids = {
                "run_status": "persistence_failed",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    state.completed_steps.append("persist_results")
    return state
