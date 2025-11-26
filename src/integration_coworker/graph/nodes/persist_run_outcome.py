"""
Persist Run Outcome Node

Per design doc Section 5.4 and Appendix C.3:
This checkpoint persists run status and metrics after build_report.

Writes to:
- run_status
- rag_eval_metrics
- repo_integrations / repo_files (if repo_root provided)

This is the final persistence checkpoint before returning the result.
"""
from datetime import datetime, UTC
import json
import logging
from typing import Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence import db

logger = logging.getLogger(__name__)


def persist_run_outcome(state: WorkflowState) -> WorkflowState:
    """
    Persist run outcome, status, and metrics.
    
    Per design doc Appendix C.3:
    - Runs after build_report
    - Records run_status, timing, errors, and RAG metrics
    - If repo_root provided, records repo integration metadata
    
    Reads: run_id, errors, completed_steps, persisted_ids
    Writes: run_status row, rag_eval_metrics rows, repo_meta (if applicable)
    """
    is_dry_run = state.options.dry_run if state.options else False
    
    # Determine final status
    if is_dry_run:
        final_status = "completed_dry_run"
    elif state.errors:
        final_status = "completed_with_errors"
    else:
        final_status = "completed"
    
    if is_dry_run:
        state.persisted_ids.update({
            "run_status": final_status,
            "run_outcome_dry_run": True,
        })
        state.completed_steps.append("persist_run_outcome")
        return state
    
    try:
        db.init_schema()
        conn = db.get_connection()  # Uses Postgres or SQLite based on config
        cur = conn.cursor()
        
        run_id = state.run_id or f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        task_id = state.persisted_ids.get("task_id")
        provider_code = state.provider_code or "unknown"
        task_slug = state.integration_task.task_slug if state.integration_task else "unknown"
        
        # 1. Insert RunStatus
        error_summary = "; ".join(state.errors[:5]) if state.errors else None  # First 5 errors
        
        cur.execute(
            """INSERT OR REPLACE INTO run_status 
               (run_id, task_id, status, started_at, finished_at, error_summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, task_id, final_status, 
             datetime.now(UTC).isoformat(),  # started_at (approximation)
             datetime.now(UTC).isoformat(),  # finished_at
             error_summary)
        )
        
        # 2. Insert RAG Eval Metrics (if we have any)
        # In future, collect actual RAG metrics from nodes
        # For now, insert a summary metric
        if state.spec_chunk_embeddings:
            cur.execute(
                """INSERT INTO rag_eval_metrics 
                   (run_id, provider_code, task_slug, node_name, metric_scope,
                    retrieved_chunk_count, used_chunk_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, provider_code, task_slug, "embed_spec_chunks", "embedding",
                 len(state.spec_chunk_embeddings), len(state.spec_chunk_embeddings))
            )
        
        # 3. Insert Repo Integration Metadata (if repo_root provided)
        if state.repo_root and state.repo_profile:
            repo_name = state.repo_root.name if hasattr(state.repo_root, 'name') else str(state.repo_root).split('/')[-1]
            profile_name = state.repo_profile.archetype if state.repo_profile else "unknown"
            
            # Upsert repo_integrations
            cur.execute(
                """INSERT INTO repo_integrations 
                   (provider_code, task_slug, repo_name, repo_root, profile_name, first_run_id, last_run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider_code, task_slug, repo_name) DO UPDATE SET
                   last_run_id = excluded.last_run_id,
                   updated_at = datetime('now')""",
                (provider_code, task_slug, repo_name, str(state.repo_root), 
                 profile_name, run_id, run_id)
            )
            cur.execute(
                "SELECT id FROM repo_integrations WHERE provider_code = ? AND task_slug = ? AND repo_name = ?",
                (provider_code, task_slug, repo_name)
            )
            integration_id = cur.fetchone()[0]
            
            # Insert repo_files for each code artifact
            for artifact in state.code_artifacts:
                cur.execute(
                    """INSERT INTO repo_files 
                       (integration_id, rel_path, artifact_type, last_run_id, last_change_type)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(integration_id, rel_path, artifact_type) DO UPDATE SET
                       last_run_id = excluded.last_run_id,
                       last_change_type = excluded.last_change_type,
                       updated_at = datetime('now')""",
                    (integration_id, artifact.rel_path, artifact.artifact_type, run_id, "create")
                )
            
            state.persisted_ids["repo_integration_id"] = integration_id
        
        conn.commit()
        conn.close()
        
        # Update persisted_ids
        state.persisted_ids.update({
            "run_status": final_status,
            "run_id": run_id,
            "run_outcome_checkpoint": "completed",
            "outcome_timestamp": datetime.now(UTC).isoformat(),
        })
        
        state.completed_steps.append("persist_run_outcome")
        logger.info(f"Run outcome persisted: run_id={run_id}, status={final_status}")
        return state
        
    except Exception as e:
        state.errors.append(f"Run outcome persistence failed: {str(e)}")
        state.persisted_ids.update({
            "run_status": "failed",
            "run_outcome_checkpoint": "failed",
            "outcome_error": str(e),
        })
        logger.error(f"Run outcome persistence failed: {e}")
        return state
