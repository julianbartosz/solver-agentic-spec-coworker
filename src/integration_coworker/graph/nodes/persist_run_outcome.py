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

V5.0: Deep instrumentation for observability
  - DB transaction tracing with operation recording
  - Step-level timing for each major phase
"""
from datetime import datetime, timezone
import logging
import time

from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence import db
from integration_coworker.persistence.sql_helpers import (
    select_by_columns, get_engine_type, placeholder
)
from integration_coworker.graph.node_trace import (
    log_step_event,
    db_transaction_context,
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
    node_name = "persist_run_outcome"
    run_id = state.run_id
    is_dry_run = state.options.dry_run if state.options else False

    # Determine final status
    if is_dry_run:
        final_status = "completed_dry_run"
    elif state.errors:
        final_status = "completed_with_errors"
    else:
        final_status = "completed"

    log_step_event(
        "node.step.start",
        node_name=node_name,
        step="determine_status",
        run_id=run_id,
        is_dry_run=is_dry_run,
        final_status=final_status,
        error_count=len(state.errors) if state.errors else 0,
    )

    if is_dry_run:
        state.persisted_ids.update({
            "run_status": final_status,
            "run_outcome_dry_run": True,
        })
        state.completed_steps.append("persist_run_outcome")
        log_step_event(
            "node.step.end",
            node_name=node_name,
            step="dry_run_skip",
            run_id=run_id,
            final_status=final_status,
        )
        return state

    try:
        # Track ops for instrumentation
        total_ops = 0
        op_start = time.perf_counter()
        commit_ms = 0.0  # V29-P01: Initialize for logging
        
        db.init_schema()
        
        with db_transaction_context(node_name, run_id=run_id) as tx:
            # V29-P01 Fix: Use context manager for connection to prevent leaks
            # Keep ALL database operations inside the context manager
            with db.get_connection() as conn:
                cur = conn.cursor()

                # Determine engine type for schema prefixes
                engine = get_engine_type()
                gold_schema = GOLD_SCHEMA if engine == "postgres" else None
                repo_schema = REPO_SCHEMA if engine == "postgres" else None
                
                tx.record_op("get_connection", {"engine": engine, "gold_schema": gold_schema is not None})

                run_id_val = state.run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                task_id = state.persisted_ids.get("task_id")
                provider_code = state.provider_code or "unknown"
                task_slug = state.integration_task.task_slug if state.integration_task else "unknown"

                # 1. Insert/Upsert RunStatus
                error_summary = "; ".join(state.errors[:5]) if state.errors else None
                run_status_table = f"{gold_schema}.run_status" if gold_schema else "run_status"
                started_at = datetime.now(timezone.utc).isoformat()
                finished_at = datetime.now(timezone.utc).isoformat()

                step_start = time.perf_counter()
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
                cur.execute(sql, (run_id_val, task_id, final_status, started_at, finished_at, error_summary))
                tx.record_op("upsert_run_status", {
                    "table": run_status_table,
                    "run_id": run_id_val,
                    "status": final_status,
                    "duration_ms": (time.perf_counter() - step_start) * 1000,
                })
                total_ops += 1

                # 2. Insert RAG Eval Metrics (if we have any)
                if state.spec_chunk_embeddings:
                    step_start = time.perf_counter()
                    rag_table = f"{gold_schema}.rag_eval_metrics" if gold_schema else "rag_eval_metrics"
                    ph = placeholder(7)
                    sql = f"""INSERT INTO {rag_table} 
                              (run_id, provider_code, task_slug, node_name, metric_scope,
                               retrieved_chunk_count, used_chunk_count)
                              VALUES ({ph})"""
                    cur.execute(sql, (run_id_val, provider_code, task_slug, "embed_spec_chunks", "embedding",
                         len(state.spec_chunk_embeddings), len(state.spec_chunk_embeddings)))
                    tx.record_op("insert_rag_metrics", {
                        "table": rag_table,
                        "chunk_count": len(state.spec_chunk_embeddings),
                        "duration_ms": (time.perf_counter() - step_start) * 1000,
                    })
                    total_ops += 1

                # 3. Insert Repo Integration Metadata (if repo_root provided)
                repo_files_count = 0
                if state.repo_root and state.repo_profile:
                    step_start = time.perf_counter()
                    repo_name = state.repo_root.name if hasattr(state.repo_root, 'name') else str(state.repo_root).split('/')[-1]
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
                         profile_name, run_id_val, run_id_val))
                    tx.record_op("upsert_repo_integration", {
                        "table": integrations_table,
                        "repo_name": repo_name,
                        "duration_ms": (time.perf_counter() - step_start) * 1000,
                    })
                    total_ops += 1

                    # Get integration_id for files
                    step_start = time.perf_counter()
                    sql = select_by_columns(
                        "integrations" if repo_schema else "repo_integrations",
                        ["id"],
                        ["provider_code", "task_slug", "repo_name"],
                        repo_schema
                    )
                    cur.execute(sql, (provider_code, task_slug, repo_name))
                    integration_id = cur.fetchone()[0]
                    tx.record_op("select_integration_id", {
                        "integration_id": integration_id,
                        "duration_ms": (time.perf_counter() - step_start) * 1000,
                    })
                    total_ops += 1

                    # Insert repo_files for each code artifact
                    files_table = f"{repo_schema}.files" if repo_schema else "repo_files"
                    step_start = time.perf_counter()
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
                        cur.execute(sql, (integration_id, artifact.rel_path, artifact.artifact_type, run_id_val, "create"))
                        repo_files_count += 1
                        total_ops += 1
                    
                    tx.record_op("upsert_repo_files_batch", {
                        "table": files_table,
                        "file_count": repo_files_count,
                        "duration_ms": (time.perf_counter() - step_start) * 1000,
                    })

                    state.persisted_ids["repo_integration_id"] = integration_id

                # Commit - V29-P01: context manager handles close after commit
                step_start = time.perf_counter()
                conn.commit()
                commit_ms = (time.perf_counter() - step_start) * 1000
            
            # tx context manager logs db.tx.commit on exit

        # Update persisted_ids
        state.persisted_ids.update({
            "run_status": final_status,
            "run_id": run_id_val,
            "run_outcome_checkpoint": "completed",
            "outcome_timestamp": datetime.now(timezone.utc).isoformat(),
        })

        state.completed_steps.append("persist_run_outcome")
        
        total_duration_ms = (time.perf_counter() - op_start) * 1000
        log_step_event(
            "node.step.end",
            node_name=node_name,
            step="persist_complete",
            run_id=run_id,
            final_status=final_status,
            total_ops=total_ops,
            repo_files_count=repo_files_count,
            commit_ms=commit_ms,
            total_duration_ms=total_duration_ms,
        )
        
        logger.info(f"Run outcome persisted: run_id={run_id_val}, status={final_status}")
        return state

    except Exception as e:
        state.errors.append(f"Run outcome persistence failed: {str(e)}")
        state.persisted_ids.update({
            "run_status": "failed",
            "run_outcome_checkpoint": "failed",
            "outcome_error": str(e),
        })
        log_step_event(
            "node.step.error",
            node_name=node_name,
            step="persist_failed",
            run_id=run_id,
            error_type=type(e).__name__,
            error_message=str(e)[:500],
        )
        logger.error(f"Run outcome persistence failed: {e}")
        return state
