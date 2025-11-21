from datetime import datetime
from integration_coworker.graph.state import WorkflowState

def persist_results(state: WorkflowState) -> WorkflowState:
    """
    Reads: All Silver drafts, All Gold drafts, Embeddings, run_id, errors, options.dry_run
    Writes: persisted_ids, run status summary
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
            "timestamp": datetime.utcnow().isoformat(),
        }
        state.persisted_ids = summary
    else:
        # For Phase 2: minimal actual persistence
        # Real implementation would use db.py and execute SQL
        
        # TODO: Connect to database
        # from integration_coworker.persistence.db import get_connection
        # conn = get_connection()
        
        # TODO: Insert SourceSystem if not exists
        # TODO: Insert SpecDocument
        # TODO: Insert Endpoints, Schemas, Entities (Silver)
        # TODO: Insert IntegrationTask, WorkflowNodes, Policies (Gold)
        # TODO: Insert run_status record
        
        # For now, just record what we would persist
        state.persisted_ids = {
            "persisted": {
                "integration_task_id": "mock_task_1",  # Would be real DB ID
                "spec_document_ids": ["mock_spec_1"],
                "endpoint_ids": [f"mock_ep_{i}" for i in range(len(state.endpoints))],
            },
            "run_status": "completed" if not state.errors else "completed_with_errors",
            "error_count": len(state.errors),
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    state.completed_steps.append("persist_results")
    return state
