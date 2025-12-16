"""
Persist Run Outcome Node

Per design doc Section 5.4 and Appendix C.3:
This checkpoint persists run status and metrics after build_report.

Writes to:
- run_status
- rag_eval_metrics
- repo_integrations / repo_files (if repo_root provided)

This is the final persistence checkpoint before returning the result.

Supports both Postgres (primary) and SQLite (fallback) using sql_helpers.
"""
from datetime import datetime, timezone
import logging

from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence import db
from integration_coworker.persistence.sql_helpers import (
    select_by_columns, get_engine_type, placeholder
)

logger = logging.getLogger(__name__)

# Schema prefixes for Postgres tables
GOLD_SCHEMA = "integration_gold"
REPO_SCHEMA = "repo_meta"


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

        # Determine engine type for schema prefixes
        engine = get_engine_type()
        gold_schema = GOLD_SCHEMA if engine == "postgres" else None
        repo_schema = REPO_SCHEMA if engine == "postgres" else None

        run_id = state.run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        task_id = state.persisted_ids.get("task_id")
        provider_code = state.provider_code or "unknown"
        task_slug = state.integration_task.task_slug if state.integration_task else "unknown"

        # 1. Insert RunStatus (upsert with update)
        error_summary = "; ".join(state.errors[:5]) if state.errors else None  # First 5 errors

        # RunStatus uses primary key so needs upsert_update
        run_status_table = f"{gold_schema}.run_status" if gold_schema else "run_status"
        started_at = datetime.now(timezone.utc).isoformat()
        finished_at = datetime.now(timezone.utc).isoformat()

        if engine == "postgres":
            sql = f"""INSERT INTO {run_status_table} 
                      (run_id, task_id, status, started_at, finished_at, error_summary)
                      VALUES (%s, %s, %s, %s, %s, %s)
                      ON CONFLICT (run_id) DO UPDATE SET
                      task_id = EXCLUDED.task_id,
                      status = EXCLUDED.status, finished_at = EXCLUDED.finished_at,
                      error_summary = EXCLUDED.error_summary"""
        else:
            sql = """INSERT OR REPLACE INTO run_status 
                     (run_id, task_id, status, started_at, finished_at, error_summary)
                     VALUES (?, ?, ?, ?, ?, ?)"""
        cur.execute(sql, (run_id, task_id, final_status, started_at, finished_at, error_summary))

        # 2. Insert RAG Eval Metrics (if we have any)
        # In future, collect actual RAG metrics from nodes
        # For now, insert a summary metric
        if state.spec_chunk_embeddings:
            rag_table = f"{gold_schema}.rag_eval_metrics" if gold_schema else "rag_eval_metrics"
            ph = placeholder(7)
            sql = f"""INSERT INTO {rag_table} 
                      (run_id, provider_code, task_slug, node_name, metric_scope,
                       retrieved_chunk_count, used_chunk_count)
                      VALUES ({ph})"""
            cur.execute(sql, (run_id, provider_code, task_slug, "embed_spec_chunks", "embedding",
                 len(state.spec_chunk_embeddings), len(state.spec_chunk_embeddings)))

        # 3. Insert Repo Integration Metadata (if repo_root provided)
        if state.repo_root and state.repo_profile:
            repo_name = state.repo_root.name if hasattr(state.repo_root, 'name') else str(state.repo_root).split('/')[-1]
            # Use archetype, or name, or fallback to "unknown" - ensure non-null
            profile_name = (
                state.repo_profile.archetype 
                or state.repo_profile.name 
                or "unknown"
            ) if state.repo_profile else "unknown"

            # Upsert repo_integrations
            integrations_table = f"{repo_schema}.integrations" if repo_schema else "repo_integrations"

            if engine == "postgres":
                sql = f"""INSERT INTO {integrations_table} 
                          (provider_code, task_slug, repo_name, repo_root, profile_name, first_run_id, last_run_id)
                          VALUES (%s, %s, %s, %s, %s, %s, %s)
                          ON CONFLICT(provider_code, task_slug, repo_name) DO UPDATE SET
                          last_run_id = EXCLUDED.last_run_id,
                          updated_at = NOW()"""
            else:
                sql = """INSERT INTO repo_integrations 
                         (provider_code, task_slug, repo_name, repo_root, profile_name, first_run_id, last_run_id)
                         VALUES (?, ?, ?, ?, ?, ?, ?)
                         ON CONFLICT(provider_code, task_slug, repo_name) DO UPDATE SET
                         last_run_id = excluded.last_run_id,
                         updated_at = datetime('now')"""
            cur.execute(sql, (provider_code, task_slug, repo_name, str(state.repo_root),
                 profile_name, run_id, run_id))

            sql = select_by_columns(
                "integrations" if repo_schema else "repo_integrations",
                ["id"],
                ["provider_code", "task_slug", "repo_name"],
                repo_schema
            )
            cur.execute(sql, (provider_code, task_slug, repo_name))
            integration_id = cur.fetchone()[0]

            # Insert repo_files for each code artifact
            files_table = f"{repo_schema}.files" if repo_schema else "repo_files"
            for artifact in state.code_artifacts:
                if engine == "postgres":
                    sql = f"""INSERT INTO {files_table} 
                              (integration_id, rel_path, artifact_type, last_run_id, last_change_type)
                              VALUES (%s, %s, %s, %s, %s)
                              ON CONFLICT(integration_id, rel_path, artifact_type) DO UPDATE SET
                              last_run_id = EXCLUDED.last_run_id,
                              last_change_type = EXCLUDED.last_change_type,
                              updated_at = NOW()"""
                else:
                    sql = """INSERT INTO repo_files 
                             (integration_id, rel_path, artifact_type, last_run_id, last_change_type)
                             VALUES (?, ?, ?, ?, ?)
                             ON CONFLICT(integration_id, rel_path, artifact_type) DO UPDATE SET
                             last_run_id = excluded.last_run_id,
                             last_change_type = excluded.last_change_type,
                             updated_at = datetime('now')"""
                cur.execute(sql, (integration_id, artifact.rel_path, artifact.artifact_type, run_id, "create"))

            state.persisted_ids["repo_integration_id"] = integration_id

        conn.commit()
        conn.close()

        # Update persisted_ids
        state.persisted_ids.update({
            "run_status": final_status,
            "run_id": run_id,
            "run_outcome_checkpoint": "completed",
            "outcome_timestamp": datetime.now(timezone.utc).isoformat(),
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
